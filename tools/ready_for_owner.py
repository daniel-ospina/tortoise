#!/usr/bin/env python3
"""``ready_for_owner`` — is this PR *truly* ready for the owner to merge from the UI?

The owner's position (2026-11-04, verbatim):

    "when PRs are truly ready for me to merge them then you open them in my regular
     chrome browser and i merge from github ui. note i am not a dev so i wont review
     them, hence the importance of the reviews before and we want them aligned with
     main so there's no regression."

He merges from the GitHub UI and **does not review**. Every defect that reaches the
merge must therefore have been caught *before* the tab opens. That failed twice on
2026-11-04 — tabs were opened for #10641 (its own ``test (a)``/``test (d)`` red,
``ai-review-gate`` **cancelled**) and #10726 (``ai-review-gate`` + lint red) — because
the list that produced those tabs checked the **merge tree** and never the **gates**.
And #10962 read ``mergeable: MERGEABLE`` while 10 of its 11 checks were ``cancelled``
or ``null``: ``mergeable`` is a tree fact, not a readiness fact.

So readiness is a COMMAND here, not a judgement. Six conditions, evaluated in order,
each printing PASS/FAIL with the RAW VALUE it read:

  1. ``tree``             ``git merge-tree --write-tree origin/main <head>`` — never the
                          ``mergeable`` flag (a stale flag is not evidence).
  2. ``gates``            the newest attempt per ``(app.slug, workflow, job)`` for every
                          required ENTRY context, and ``success`` is the ONLY pass.
                          ``merge_conditions`` names are DEFERRED at a PR head ONLY
                          when no PR-triggered run of the emitting workflow is on that
                          head (then Mergify settles them on the queue branch). A name
                          whose workflow DID run here is graded `MISSING` (pending or
                          never posted — absence is not deferral) unless it is green, and
                          an unattributable absence is a FAIL. See ``check_gates``.
  3. ``review``           ``record-review.sh --check-carry`` bound to the LIVE head.
  4. ``coverage``         the CI selection covers the union of the touched files; a
                          hand-picked subset of a declared union is refused.
  5. ``exclusions``       the declared exclusions (#10226, a reversal awaiting an
                          ``OVERRIDES:`` line, a conflicting flag with a dirty tree, a
                          head younger than 15 minutes).
  6. ``blocking-finding`` no open P0/P1/P2 finding posted after the last clean verdict.

Output is exactly one of::

    READY-FOR-OWNER(<PR>, <full head sha>)      # exit 0, all six PASS
    NOT-READY(<PR>, failed=<condition,...>)     # exit 1, with each failing raw value
                                                # printed on its own check line above

``--required-set`` is the ONE exception, and it is a different QUESTION ("what is required
here?", not "is this PR ready?"), so it is a different output shape by design::

    source: … / mode: … / queue_conditions: … / merge_conditions: … / union (N): …
                                                # exit 0, the derived set
    REQUIRED-SET-CONFIG-ERROR(...)              # exit 2, the two sources disagree
    REQUIRED-SET-UNREADABLE(...)                # exit 2, could not be read

It takes no PR number and needs no credential, and it never emits either of the two lines
above — a caller grepping for READY/NOT-READY cannot mistake one for the other.

⛔ **It must never print READY on a partial read.** An input that could not be read is
``FAIL(unreadable)`` — never assumed, never skipped, never defaulted to green. That is
the whole point of the tool: a human who does not review relies on this thing saying no.

Why ``success`` is the only pass, and why ``skipped``/``neutral`` are not
------------------------------------------------------------------------
GitHub's own branch protection accepts ``success``, ``skipped`` and ``neutral`` as
passing statuses. This repo deliberately does NOT, and the difference is recorded here
rather than assumed:

* ``.mergify.yml`` (the mechanism that actually merges) uses ``check-success`` for every
  entry/merge condition, and its header says why: *"a skipped or neutral check must not
  read as a pass"*. A queue entry gate weaker than the queue it gates is a false PASS.
* The owner does not review. A required check that CONCLUDED WITHOUT EXERCISING CODE
  (``skipped``/``neutral``) or was CANCELLED measured nothing, so it is not evidence
  that the tree is sound. Absence is not a pass **for an ENTRY context** — the
  fail-closed rule this file shares with ``/v1``-style convergence (see ``docs``/
  ``AGENTS.md`` check-run polarity). A ``merge_conditions`` name absent at the head is a
  different case, and it is DEFERRED **only when nothing shows the emitting workflow ran
  here** — see condition 2 above for the attributed cases.

The strict direction holds for a MEASURED result, and it is worth stating its edge
precisely rather than absolutely: this tool never treats ``skipped``/``neutral``/
``cancelled``/an unknown conclusion as a pass where ``.mergify.yml``'s ``check-success``
would refuse, so it cannot allow a merge for a reason the queue would reject. What it does
NOT claim is that the queue will accept the merge: a ``merge_conditions`` check whose
emitting workflow did not run on the author head is DEFERRED there and settled by Mergify on
``mergify/merge-queue/<sha>`` (one whose workflow DID run here must be green — absence is a
FAIL). READY therefore means *every ENTRY context passed here, and every merge-time context
was either measured green here or genuinely deferred* — never *the queue will merge this*.

Grouping is NOT re-derived here
-------------------------------
``(app.slug, workflow identity, job name)`` grouping, latest-attempt-by-``id`` ordering,
voided-red handling and the unrecognised-conclusion polarity all live in
``tools/ci_verdict.py`` — the repo's single authoritative check-run reading (#5042).
This tool imports it rather than growing a second definition, and applies only its own
STRICTER predicate over the groups it returns (``success`` only, for required contexts
only). Two predicates over one grouping is the design; two groupings would be the
divergence #5042 exists to remove.

Live use::

    python tools/ready_for_owner.py 10962
    python tools/ready_for_owner.py 10962 --json
    python tools/ready_for_owner.py 10962 --repo daniel-ospina/tortoise
    python tools/ready_for_owner.py --required-set              # what is REQUIRED here
    python tools/ready_for_owner.py --required-set --ref HEAD   # …as this BRANCH's last commit declares

⛔ ONE RESIDUAL IN THE GATE READ, NAMED RATHER THAN HIDDEN: the required set is DERIVED
from `.mergify.yml` and carries check NAMES only, while `ci_verdict`'s ``GroupVerdict``
carries the app SLUG (not its numeric id), so a required name is satisfied by a
same-named ``success`` from another app. Inert in this repo — GitHub Actions is the only
publisher of all seven names — and closing it needs an ``app.id`` on ``GroupVerdict``, i.e.
a change to the ONE check-run reading (#5042) rather
than a second grouping here.

⛔ ONE KNOWN LIMITATION, NAMED RATHER THAN HIDDEN: condition 3 asks
``record-review.sh --check-carry``, and the review RECORD is per-machine
(``$HOME/.pi/agent/reviews``). A review minted on another box is therefore invisible to
the default read, so such a PR reads ``NOT-READY(review)`` — the safe direction, but it can
cost a re-review the fleet already paid for. The caller-supplied pair (``--recorded-diff``/``--recorded-head``) is NOT forwarded: the
recorder's supplied-pair arm short-circuits its record lookup, so forwarding it made this
arm a self-attestation (measured on #10974). The gate asks the box and FAILS when the box
has nothing.

Read-only against other PRs. It writes nothing but its own stdout: no PR comment, no
label, no merge. (A lane that cannot write to PRs — #10930 — can still run it.)

Bounded commands: there is no ``timeout``/``gtimeout`` on macOS, so this tool does its
own bounding with ``subprocess`` timeouts (``GH_TIMEOUT``/``GIT_TIMEOUT``). API reads
are cached for the life of one run, so a checked PR costs one read per endpoint.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/ready_for_owner.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/ready_for_owner.py`"
    )

import argparse
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import ci_verdict  # noqa: E402  (path set up above)
from tools.mergify_config_guard import (  # noqa: E402  (path set up above)
    # One definition of the PR-scoped triggers, never a second copy that could drift.
    PR_TRIGGERS,
    GuardUnreadable,
)
from tools.required_set import (  # noqa: E402  (path set up above)
    RequiredSet,
    RequiredSetConfigError,
    RequiredSetUnreadable,
    derive_from_config,
    derive_from_ref,
)

# ── the contract's constants ────────────────────────────────────────────────

CONDITIONS: tuple[str, ...] = (
    "tree",
    "gates",
    "review",
    "coverage",
    "exclusions",
    "blocking-finding",
)

#: The ONLY conclusion that passes a required context. See the module docstring.
REQUIRED_CONCLUSION = "success"

#: A ``merge_conditions`` name is DEFERRED at a PR head **only when there is no evidence
#: its emitting workflow ran here** — Mergify settles those on ``mergify/merge-queue/<sha>``.
#: ⛔ NOT "absent is deferred": ``python-ci-gate`` DOES report on author PR heads, and a
#: ``needs``-gated job has no check-run until its prerequisites finish, so an absent
#: aggregate is indistinguishable from a PENDING one. The 2026-11-04 "every sampled PR head
#: carried ZERO runs" reading is RETRACTED (`tools/required_set.py`: it was taken while the
#: matrix was still running) — do not restore the unconditional deferral from it, that was
#: the fail-open `_merge_time_absence` now closes. Absence with a run of the emitter on this
#: head is ``MISSING`` (a FAIL); absence with no attribution at all is a FAIL.
DEFERRED = "DEFERRED(merge-time)"

#: A head younger than this is still settling: CI may not have been scheduled, the
#: review record may not have been minted, and the queue has not seen it. Opening the
#: owner's tab on a 10-second-old head is the trap this bound closes.
HEAD_MIN_AGE = timedelta(minutes=15)

DEFAULT_REPO = "daniel-ospina/tortoise"
PROTECTED_BRANCH = "main"

#: PRs that must NEVER be declared ready, with the reason each is here. A declared
#: exclusion is an auditable list, not a heuristic: adding an entry is a deliberate act,
#: and the tool quotes the reason verbatim so the owner can see why it refused.
DECLARED_EXCLUSIONS: Mapping[int, str] = {
    # #10226: the #4282 26-method SDK deletion. It is a DRAFT, it changes the MCP/SDK
    # surface (which needs Daniel's explicit approval, not a readiness judgement), and it
    # carries 39 review rounds with an in-progress merge-up. It is never a routine
    # "merge from the UI" candidate, whatever its checks say.
    10226: (
        "declared exclusion: the #4282 SDK-surface deletion is a draft surface change "
        "awaiting an explicit owner decision, not a routine merge-from-the-UI candidate"
    ),
}

#: Labels that make the QUEUE refuse a PR at ENTRY (`.mergify.yml` `queue_conditions`).
#: `superseded-by-fold` is the only non-check entry there; it is listed here by name so a
#: change to that list is a visible edit rather than a silent divergence.
QUEUE_BLOCKING_LABELS = frozenset({"superseded-by-fold"})

#: A reviewer's blocking finding, in the shape ``scripts/review-pr.sh`` defines and
#: ``code-review`` converges on: a line that LEADS with ``[P0]``/``[P1]``/``[P2]``
#: (``[P1] tortoise/sdk.py:12 — …``). The token must be the line's FIRST word, which is
#: what makes this a finding rather than prose that happens to mention a severity.
#: ⛔ QUOTING A FINDING (`> [P1] …`) MUST STILL BLOCK — answering a finding by quoting
#: it is normal review behaviour, and a `>` prefix read NEUTRAL (round 9). So do nested
#: bullets and bold brackets.
#: ⛔ THE THREE SHARED CLASSES `scripts/review-pr.sh` keys on. Every arm below composes
#: THESE, instead of enumerating a neighbourhood: that enumeration is the ratchet that made
#: `2 remaining P1` block while `Three remaining P1` did not, and made `2 open P1 findings
#: remain` block while `4 defects remain unresolved` did not.
NUMERAL = r"(?:[0-9]+|one|two|three|four|five|six|seven|eight|nine|ten|several|multiple)"
FINDING_NOUN = (
    r"(?:findings?|blockers?|issues?|defects?|bugs?|failures?|errors?|regressions?"
    r"|vulnerabilit(?:y|ies)|violations?|problems?)"
)
#: A severity token, `s`-PLURAL INCLUDED — `P1s: 2` recorded a signed clean record in
#: `review-pr.sh`'s own history because neither reader knew the plural form.
SEV = r"P[0-2]s?"

#: `[P1]` OR `(P1)` — `scripts/review-pr.sh:1363` (`[\[(][Pp][0-2][\])]`) treats a
#: parenthesised label as decorated, and a live body (#10902) writes `- **F1 (P2)**`.
BLOCKING_RE = re.compile(
    r"(?m)^\s*(?:[-*>]+\s*|[0-9]+[.)]\s*)*\**`?(?:\[|\()?(P0|P1|P2)s?(?:\]|\))?\**`?\s*(?=\S|$)"
)
ISSUES_RE = re.compile(r"^\s*ISSUES\s+(\d+)\b", re.MULTILINE)
#: A CLEAN outcome. ⛔ THE POSITIVE VOCABULARY IS THE RECORDER'S TOO: `Outcome: clean` is
#: the declaration `scripts/record-review.sh` reads (its own suppress arm keys on it), and
#: recognising only "NO ISSUES FOUND"/"CLEAN" left the fleet's dominant format invisible —
#: measured on #10962, two clean verdicts in that format left `last-clear=-`, so once any
#: finding had been posted the PR could NEVER be certified (a permanent false NOT-READY).
CLEAR_RE = re.compile(
    r"(?im)(?:^\s*CLEAN\b|NO ISSUES FOUND|(?:^|[*\s])outcome:[\t ]*[*\s]*clean\b)"
)
#: ⛔ A CLEAR MUST BE A VERDICT, NOT A CITATION OF ONE. `CLEAR_RE` matches the bare phrase
#: ANYWHERE in a body, and `_read_comments` feeds it every artifact — so a STATUS comment that
#: merely quotes a past clean verdict closed an open blocker. Measured on #10929, comment
#: `6636540079` (*"## Merge blocked on runner capacity — the exact mechanism, measured"*):
#: *"Six fresh-context review rounds; the last returned **`NO ISSUES FOUND`** (comment above)."*
#: `CLEAR_RE` matched the quoted phrase, `_grades_as_blocking` correctly called the body not a
#: finding, and condition 6 returned PASS over a P1 posted before it — `READY-FOR-OWNER` on the
#: owner's tab for a PR carrying an open P1, the exact false PASS this condition exists to stop.
#: The SHAPE decides, not the phrase: the clear must STAND AS the artifact's verdict — its own
#: line (optionally emphasised or blockquoted) or introduced by a verdict cue. That keeps every
#: real verdict form in the fleet's corpus (`**NO ISSUES FOUND**`, `Reviewer verdict: NO ISSUES
#: FOUND`, `Re-review: NO ISSUES FOUND at the new head.`, `**Outcome: CLEAN.**`) and rejects the
#: citation, whose phrase is embedded in prose ABOUT the commentary that reported it.
CLEAR_VERDICT_RE = re.compile(
    r"(?im)(?:^[ \t>*_]*CLEAN\b"
    r"|^[ \t>*_]*NO ISSUES FOUND\b"
    r"|\b(?:verdict|outcome|result|conclusion|review|reviewed|re-?review|final)\b"
    # ⛔ THE CUE ARM ACCEPTS A BACKTICK SEPARATOR; the two LINE-START arms above do NOT.
    # A code-formatted verdict is how the fleet actually posts them — `## Fresh-context review
    # — `NO ISSUES FOUND` at `ab60fa6a3`` and `### Verdict\n\n`CLEAN` — at `d6f5d3f3c`` — and
    # 2 of the 6 real clean reviews on PR #10975 are in that shape, so they read NEUTRAL and
    # could never close a finding. It is deliberately NOT added to `^[ \t>*_]*`: the cited
    # form pinned by `test_a_quoted_verdict_is_not_a_verdict` starts a line with `**`NO ISSUES
    # FOUND`**` and MUST stay a citation, which that arm is the only thing refusing.
    r"[\s:*_\u2014\u2013`]{0,4}(?:NO ISSUES FOUND|CLEAN)\b)"
)
#: ⛔ THE SUFFIX THAT FOLLOWS THE VERDICT DECIDES WHETHER IT WAS ASSERTED OR REPORTED — and
#: it must be read as a WINDOW, not as the next byte. `.match(text, m.end())` anchors at the
#: character straight after the phrase, so a single `—`, `.`, `;`, `:` or closing `**` between
#: the verdict and its retraction made the retraction INVISIBLE: `**Verdict: CLEAN** — though
#: only before the last three commits`, `**Verdict: CLEAN.** That was the previous head`,
#: `verdict: CLEAN - no longer holds` and `**Verdict: NO ISSUES FOUND** — except the P1 below`
#: all cleared an open P1. `CLEAR_VERDICT_RE` already tolerates an em dash in its OWN separator,
#: so the file knows punctuation sits there. Read the REST OF THE LINE (`_retracted_from`).
#:
#: The categories, not the spellings — a guard written against one phrasing leaves its
#: neighbours open:
#:   1. the verdict is said to no longer hold / to be superseded;
#:   2. it is attributed to a DIFFERENT artifact (a previous head, review, round, record);
#:   3. it is qualified by a live exception (`except`, `apart from`, `unless`, `subject to`);
#:   4. the suffix still declares an open severity — a bare `P0|P1|P2` token, which every one
#:      of the reported false passes carried and no legitimate clear form in this repo's corpus
#:      does unnegated (`no P1` / `without P1` / `none of the P1` are excluded).
#:
#: ⛔ AND DO NOT REJECT ON A BARE CONTRASTIVE (`but`, `however`, `though`, `yet`):
#: `Verdict: CLEAN, but three P3 nits recorded.` and `Verdict: CLEAN though I noted two P3
#: nits.` are CLEARS — a P3 is not a blocker — and an earlier draft of this guard refused both.
_RETRACTION_RULES_1_TO_3 = (
    # (1) no longer the case
    r"no\s+longer\b"
    # ⛔ THE COPULA STAYS BARE — FAIL-CLOSED IS THE DEFAULT (review #10975, twice).
    # An earlier attempt in this lane REPLACED this with a whitelist of "disposition"
    # predicates (fixed|resolved|…), which inverted the polarity: anything NOT in the list
    # stopped retracting, so `The P1 was fixed, but the fix is not correct.` became a CLEAR
    # over an open P1 — a FAIL-OPEN, measured as 9 such bodies, the one class this file
    # treats as P0. A whitelist of English is fail-open by construction; a blacklist is not.
    # So the negation is EXEMPTED, not enumerated: `is not <badness>` is good news
    # (`the pin is not vacuous`, `this is not a blocking problem`) and those are the only
    # shapes carved out. Everything else — including `not correct`, `not safe`, `not tested`,
    # `not covered`, `not settled` — still retracts, and an omission costs an over-block (the
    # acceptable direction), never a false PASS.
    r"|(?:is|was|are|were)\s+(?:now\s+)?not\b"
    r"(?!\s+(?:yet\s+)?(?:vacuous|a\s+blocking|a\s+blocker|a\s+problem|a\s+finding"
    r"|a\s+result|a\s+defect|a\s+concern|a\s+regression|a\s+surprise|an\s+issue|an\s+error"
    r"|a\s+showstopper)\b)"
    r"|(?:is|was|are|were)\s+(?:now\s+)?(?:superseded|stale|outdated|obsolete|invalid|wrong|moot)\b"
    r"|superseded\b|stale\b|outdated\b|obsolete\b|invalid\b|moot\b"
    r"|predat(?:e|es|ed|ing)\b|was\s+returned\b|had\s+been\b"
    # (1b) the finding is said to be ABSENT, not fixed — `no evidence the P1 was resolved`
    # otherwise reads as a COMPLETION (`was resolved`) and the severity arm stands down.
    r"|\bno\s+(?:evidence|proof|sign|indication|mention|attempt)\b"
    # ⛔ `no test` IS ALSO FAIL-CLOSED, AND THE EXEMPTION IS THE INSTRUMENT *DOING* SOMETHING.
    # `no test for the P1` is a LIVE finding (the severity arm's own comment says so) and must
    # keep retracting; what must not is prose about the REVIEW ITSELF — `no test suite was
    # executed`, `no test was run here` — which retracted 6 real clean reviews on this PR.
    # ⛔ THE NOUN ALONE IS NOT THE EXEMPTION (review #10975, cycle 3): keying on `suite|run`
    # cleared `No test suite covers the guard path.` and `No test has been written for the
    # guard.` — the same noun with the OPPOSITE subject, i.e. a coverage gap about the diff.
    # What makes it the reviewer's instrument is an EXECUTION predicate after it
    # (was/were/is/are/has been/had been + executed/run/possible/attempted/available/here), so
    # the exemption grades the phrase, not the vocabulary.
    r"|\bno\s+test\b(?!\s+(?:suite\s+|run\s+)?(?:was|were|is|are|has\s+been|had\s+been)\s+"
    r"(?:actually\s+|ever\s+|here\b|possible\b|executed\b|run\b|attempted\b|available\b))"
    r"|\bno\s+one\s+(?:has|have|did)\b|\bhas\s+since\b|\bhas\s+moved\b|\bsince\s+moved\b"
    # (2) attributed to another artifact / a former state of this one
    r"|(?:previous|earlier|prior|old)\s+(?:head|record|round|assessment|verdict|review)\b"
    r"|that\s+(?:was|is)\s+the\s+(?:previous|earlier|prior|old)\b"
    r"|only\s+(?:before|until|as\s+of)\b|before\s+the\s+(?:last|previous|latest)\b"
    # (3) a live exception to the clear
    r"|except\b|apart\s+from\b|other\s+than\b|aside\s+from\b|besides\b"
    r"|unless\b|subject\s+to\b|provided\s+that\b"
    # (4) a still-live finding, declared in words
    r"|unfixed\b|unaddressed\b|still\s+open\b|remains?\s+open\b|not\s+fixed\b"
)
#: The same source SPLIT BY RULE, so the two windows below can grade DIFFERENT subsets without
#: the pattern text drifting apart. `_R1 + _R2 + _R3 + _R4_WORDS` is byte-identical to
#: `_RETRACTION_RULES_1_TO_3`; a pin in the test file asserts that, because two copies of a
#: regex that must agree is exactly how one of them rots unnoticed.
_R1_NO_LONGER = (
    r"no\s+longer\b"
    # Same blacklist (not whitelist) as `_RETRACTION_RULES_1_TO_3`, which must stay
    # byte-identical — see the comment there for why the enumeration polarity matters.
    r"|(?:is|was|are|were)\s+(?:now\s+)?not\b"
    r"(?!\s+(?:yet\s+)?(?:vacuous|a\s+blocking|a\s+blocker|a\s+problem|a\s+finding"
    r"|a\s+result|a\s+defect|a\s+concern|a\s+regression|a\s+surprise|an\s+issue|an\s+error"
    r"|a\s+showstopper)\b)"
    r"|(?:is|was|are|were)\s+(?:now\s+)?(?:superseded|stale|outdated|obsolete|invalid|wrong|moot)\b"
    r"|superseded\b|stale\b|outdated\b|obsolete\b|invalid\b|moot\b"
    r"|predat(?:e|es|ed|ing)\b|was\s+returned\b|had\s+been\b"
    r"|\bno\s+(?:evidence|proof|sign|indication|mention|attempt)\b"
    r"|\bno\s+test\b(?!\s+(?:suite\s+|run\s+)?(?:was|were|is|are|has\s+been|had\s+been)\s+"
    r"(?:actually\s+|ever\s+|here\b|possible\b|executed\b|run\b|attempted\b|available\b))"
    r"|\bno\s+one\s+(?:has|have|did)\b|\bhas\s+since\b|\bhas\s+moved\b|\bsince\s+moved\b"
)
_R2_ATTRIBUTED = (
    r"|(?:previous|earlier|prior|old)\s+(?:head|record|round|assessment|verdict|review)\b"
    r"|that\s+(?:was|is)\s+the\s+(?:previous|earlier|prior|old)\b"
    r"|only\s+(?:before|until|as\s+of)\b|before\s+the\s+(?:last|previous|latest)\b"
)
#: ⛔ RULE (3) IS THE ONE THAT DOES NOT DETACH. `except`, `apart from`, `besides`, `unless`
#: describe the verdict when they sit beside it, but in the PARAGRAPH AFTER one they are
#: ordinary English — `no other files changed except the fixture` retracts nothing — so a
#: window that carried rule (3) into the next paragraph refused real clears (the #10962
#: direction, measured history). It is graded over the verdict's OWN paragraph only.
_R3_EXCEPTION = (
    r"|except\b|apart\s+from\b|other\s+than\b|aside\s+from\b|besides\b"
    r"|unless\b|subject\s+to\b|provided\s+that\b"
)
_R4_WORDS = r"|unfixed\b|unaddressed\b|still\s+open\b|remains?\s+open\b|not\s+fixed\b"
#: Rule (4)'s SEVERITY arm — the bare tokens, and the ones a reviewer actually writes.
#: ⛔ THIS ARM IS GRADED PER TOKEN, NOT PER LINE. An earlier draft stood the whole arm down
#: whenever a negation word appeared ANYWHERE in the suffix, so one `no` anywhere erased a live
#: P1 — `**Verdict: CLEAN** — there is no test for the P1.` cleared while
#: `**Verdict: CLEAN** — the P1 was not resolved.` blocked, one word apart. The exemption now
#: has to GOVERN the token it exempts.
_SEVERITY_TOKEN_RE = re.compile(r"(?i)\b(?:P[0-2]s?|blockers?|priority\s+[0-2]s?)\b")
#: ⛔ A BLANK LINE IS A LINE WHOSE ONLY CONTENT IS WHITESPACE — not just `\n\n`. A body written
#: in a browser or pasted from a terminal carries `\r\n\r\n`, and an editor that leaves a space
#: on an otherwise empty line gives `\n \n`. `text.find("\n\n", …)` sees NEITHER, so the
#: paragraph boundary collapsed and a live P1 on the next screen was invisible while the same
#: sentence after a bare `\n\n` blocked — the whole fail-open returning in one keystroke
#: (review cycle 7, finding 1).
_BLANK_LINE_RE = re.compile(r"\n[ \t\r\x0b\x0c\xa0]*\n")
#: Rules (1)–(3) plus rule (4)'s WORD arm, compiled once and graded over the verdict's OWN
#: paragraph (rules (1)–(4) in full — rule (3)'s exception words are anchored to the verdict
#: THERE, and only there; see `_NEXT_PARA_RETRACTION_RE`).
_RETRACTION_NO_SEVERITY_RE = re.compile(r"(?i)(?:" + _RETRACTION_RULES_1_TO_3 + r")")
#: ⛔ RULE (4)'s WORD ARM IS **NEVER WINDOWED** (review #10975, cycle 3 — a P0 I introduced).
#: It is a CLOSED vocabulary of finding-STATE words (`unfixed`, `unaddressed`, `still open`,
#: `remains open`, `not fixed`). A window cannot know whose subject a phrase has, so bounding it
#: let `**Verdict: CLEAN.**\n\nGood.\n\nAlso good.\n\nThat path is unfixed.` — and the sharper
#: `Fixed the P1 at 4f450ae81 — but the regression is back and that path is unfixed.`, where the
#: `Fixed the …` completion stands the severity arm down — CLOSE a live P1. Measured cost of
#: grading it over the whole suffix again, on this PR's 54 real comments: exactly ONE body
#: re-blocks (a fix-lane status comment that CITES `review CLEAN (nothing to apply)` while
#: quoting a prior "not fixed in the fold" disposition — a body that correctly should not
#: close anything), and ZERO genuine clean reviews are lost.
#: ⛔ ...AND THE FINDING-STATE PHRASES ARE GRADED OVER THE WHOLE SUFFIX TOO. Bare `no longer`
#: has to stay windowed because real reviews use it as code prose ("`lane_key` can no longer
#: raise"); the PHRASE `no longer holds` has no such reading, so it is graded everywhere. Without
#: this arm, `**Verdict: CLEAN.**\n\nRead the diff.\n\nI fixed the P1 in 4f450ae81.\n\nOn
#: re-read, my verdict no longer holds.` closed a live P1 (measured, review #10975 cycle 3).
_SUFFIX_RETRACTION_RE = re.compile(
    r"(?i)(?:"
    + _R4_WORDS.lstrip("|")
    + r"|no\s+longer\s+(?:the\s+case|holds|valid|accurate|true|applies|stands)\b)"
)

#: ⛔ THE PARAGRAPH AFTER THE VERDICT IS GRADED NARROWER THAN THE VERDICT'S OWN, AND IT IS
#: GRADED FOR ONE PARAGRAPH — NOT TO THE END OF THE BODY (review #10975, P1).
#: Rules (1)–(4) except (3) are about the VERDICT's own state (`no longer holds`, `still open`,
#: `previous head`), so they belong beside it. Graded to the END, they are matched by any later
#: mention of `stale` / `superseded` / `no longer` ANYWHERE in a 150-line review — which
#: retracted the verdict of the review that wrote it. MEASURED on this PR: 11 of the 14 real
#: clean review comments graded `last-clear=-`, with triggers thousands of characters downstream
#: (`is not exercised` about the reviewer's own External rung, `no test suite was executed`
#: about the box, `the previous record is stale`).
#: ⛔ WHAT MAY BE WINDOWED IS ONLY THE SUBJECT-AMBIGUOUS HALF — `no longer` (real code prose:
#: "`lane_key` can no longer raise"), the bare `stale|superseded|…` words, and rule (2)'s
#: attribution (`previous head`, `prior review`). Rule (4)'s closed state vocabulary is NOT,
#: per `_R4_WORDS_SUFFIX_RE` above.
#: ⛔ THE CLIFF THIS ONCE GUARDED AGAINST IS COVERED BY A DIFFERENT LAYER. An older revision
#: stopped at exactly one paragraph and left `CLEAN.\n\nFine.\n\nI count one P1` clearing while
#: the fourth-newline spelling blocked. That body NAMES a severity token, and the per-token
#: severity arm below is graded over the WHOLE suffix (never windowed), so both spellings still
#: block, for the reason that actually applies. A token is a token wherever it sits.
_NEXT_PARA_RETRACTION_RE = re.compile(r"(?i)(?:" + _R1_NO_LONGER + _R2_ATTRIBUTED + r")")
#: ⛔ IT MAY NOT CROSS A CONTRASTIVE, A CLAUSE BREAK, OR A COMMA (review cycle 8). A plain
#: character window reached from `fixed` in `the earlier P2 was fixed, but the P1 is still
#: there` all the way to the P1 and exempted the very finding the sentence warns about; the
#: comma form is the same defect one word over: `Fixed the P2, the P1 needs work.`
_SEVERITY_COMPLETED_BEFORE_RE = re.compile(
    r"(?i)\b(?:closed|closes|fixed|fixes|resolved|resolves|addressed|addresses|landed)\b"
    r"(?:(?!\bbut\b|\bhowever\b|\bthough\b|\byet\b)[^.!?,;\n]){0,60}\Z"
)
#: A ZERO-COUNT is the other way to clear a severity in a status comment: `P1: none`,
#: `P1 findings: 0`, `P2s — nil`. This module already reads that shape for the report side
#: (`ZERO_SEVERITY_COUNT_RE`), so a clear guard that could not see it refused real clears.
_SEVERITY_ZERO_COUNT_RE = re.compile(
    r"(?i)\s*(?:findings?|issues?|blockers?)?\s*[:—–-]\s*(?:none|0|zero|nil|n/a)\b"
)
#: The clear spelling for a token: `no P0/P1/P2` / `without P1` — a negation IMMEDIATELY before
#: it. It must be immediate, and it must be able to see the LIST the negation governs and the
#: QUALIFIERS a writer puts inside it: `no P0, P1 or P2`, `no P0/P1/P2`, `No new P1 findings`,
#: `No remaining blockers`, `Zero outstanding blockers`. A window that accepted only `[/,]`
#: lists and the word `new` refused every other qualifier — real clears, dismissed on a
#: spelling (review cycles 7 and 8). It must NOT be permissive enough to run away from the
#: negation: `no test for the P1` is a LIVE finding.
_SEVERITY_NEGATED_BEFORE_RE = re.compile(
    r"(?i)(?:\bno|\bwithout\b|\bzero\s+of\b|\bzero\b|\bnone\s+of)\b"
    r"(?:\s+(?:new|remaining|outstanding|other|further|open))?\s+(?:the\s+)?"
    r"(?:P[0-2]s?\s*(?:[/,]|\bor\b|\band\b)?\s*)*\Z"
)
#: The other clear spelling: the token is followed by a COMPLETION — `the P2 findings are
#: closed`, `the P1 has been addressed`, `the P1 and the P2 are both addressed now`. ⛔ `been`
#: and intervening adverbs are NOT optional: omitting them refused the two most natural
#: completions there are, which is a permanent false NOT-READY in the #10962 class. The
#: coordination group is not optional either — a coordinated completion puts the verb after the
#: SECOND token, so `the P1 and the P2 are both addressed now` must exempt the `P1` too.
_SEVERITY_COMPLETED_AFTER_RE = re.compile(
    r"(?i)(?:\s*(?:,|and|or)\s*(?:the\s+)?P[0-2]s?)*\s*"
    # A noun may qualify the finding between it and its completion: `The P1 fix landed in
    # 4f450ae81.` — `landed` is the completion, `fix` is the subject the token modifies. The
    # same slot carries the finding's PROVENANCE: `the P1 from cycle 3 is gone`. Both are
    # anchored and bounded, so they cannot drift into the character-window bug below.
    r"(?:\s+(?:fix|patch|change|update|remediation|resolution|findings?|issues?))?\s*"
    r"(?:\s+from\s+\w[\w/ ]{0,18})?\s*"
    # ⛔ THE VERB IS OPTIONAL because the elliptical form is real (`no P1 outstanding, all P2s
    # closed`). It must therefore be ANCHORED to the token — see `_retracted_from`, which uses
    # `match`, not `search`. With a `search` over a character window any `fixed|closed|…` sixty
    # characters away stood the arm down, so `The P1 was never fixed.` CLEARED (review cycle 8,
    # P0). Anchored, the phrase has to start at the token.
    r"(?:\b(?:is|are|was|were|has|have|had)(?:\s+been)?"
    r"(?:\s+(?:now|both|all|already|since))?\s+)?"
    r"(?:closed|fixed|resolved|addressed|gone|landed)\b"
)
#: ⛔ A COMPLETION ON THE LEFT MAY NOT GOVERN A TOKEN THAT STARTS ITS OWN PREDICATE. In
#: `Fixed the P2 and the P1 needs work.` the completion verb plainly governs the P2, and the P1
#: is the SUBJECT of a new clause that says it is still live — but a left window cannot tell that
#: from `I fixed the P1 and the P2 in 4f450ae81.`, where one completion governs both. The
#: predicate after the token is what separates them, and it is why this arm is skipped there
#: (`the P2 are both addressed now` still clears through `_SEVERITY_COMPLETED_AFTER_RE`).
_SEVERITY_PREDICATE_AFTER_RE = re.compile(
    r"(?i)\s*(?:needs?|requires?|remains?|is|are|was|were|still|unresolved|open|unfixed|not"
    r"|has|have|had)\b"
)
#: ⛔ THE NEGATED ARM'S COMPLETION PREDICATE. `_SEVERITY_NEGATED_BEFORE_RE` exempts a token whose
#: left neighbour is `no|without|zero|none of` — an ABSENCE spelling. That is only sound while the
#: negation governs the TOKEN as a noun. When a COMPLETION verb is attached to the token, the
#: negation is reaching the VERB, and the sentence says the completion did not happen: `without
#: the P1 being fixed`, `None of the P1 findings were addressed`, `No P2 was fixed`, `zero of the
#: P1 findings were closed`. Those are LIVE findings, and before this regex they cleared.
#:
#: It is deliberately NARROWER than `_SEVERITY_COMPLETED_AFTER_RE`: it accepts the passive and
#: progressive spellings that regex cannot (`being fixed`, `were addressed`), and it does NOT
#: accept a liveness predicate — `No P1 remains.` / `No P1 is open.` are real clears, so widening
#: the liveness side here would reintroduce the #10962 false NOT-READY in the other direction.
#:
#: ⛔ AND ITS SLOTS ARE CLASSES, NOT SPELLINGS (fold review, P1). The first version enumerated the
#: adverbs between the auxiliary and the verb (`been|now|already|both|all|yet`), so every other
#: adverb re-opened the same false PASS — measured through `check_blocking_finding`: `No P1 was
#: ever fixed.`, `…actually addressed.`, `…fully resolved.`, `None of the P1 items was properly
#: addressed.` all PASSed and printed `READY-FOR-OWNER`. The slot is now "up to three plain words"
#: — bounded, and forbidden from crossing a coordinator or clause punctuation, so it cannot run
#: away from the token — because the CATEGORY is the finding and an enumerated list of adverbs is
#: the anti-pattern this file names at `:237` ("The categories, not the spellings").
_SEVERITY_COMPLETION_NEGATED_AFTER_RE = re.compile(
    r"(?i)\s*"
    # the noun the token modifies (`None of the P1 findings were …`, `No P1 changes were …`)
    r"(?:(?:findings?|issues?|items?|ones?|blockers?|fixes|fix|patches?|patch|changes?|change)"
    r"\s+)?"
    # the copula/auxiliary that carries the completion (`were`, `is`, `has been`, `being`)
    r"(?:(?:is|are|was|were|has|have|had|being|be)\s+)?"
    # ANY intervening adverbs/auxiliaries, bounded to FOUR words and confined to the same clause.
    # ⛔ FOUR, and `yet` is NOT a coordinator here: a fresh review measured `No P1 is yet to be
    # addressed.` and `None of the P1 findings have as of today been addressed.` clearing an open
    # P1, because the slot was ONE word and `yet` sat in the blacklist. `yet` is contrastive in
    # `was fixed, yet the fix is wrong` — but that spelling does not need this arm, which is only
    # reachable behind a negation.
    r"(?:(?!\b(?:and|but|or|however|so)\b)[^\W\d_]+\s+){0,4}"
    # THE COMPLETION ITSELF. Only a completion — never a liveness word.
    r"(?:closed|fixed|resolved|addressed|landed|gone|made)\b"
)
#: ⛔ AND `no/without/zero/none of` IS NOT THE ONLY WAY TO SAY IT (fold review, P1).
#: `Not one P1 was fixed.` / `Not a single P1 was addressed.` / `Nothing about the P1 was fixed.`
#: are the same negation in a form `_SEVERITY_NEGATED_BEFORE_RE` cannot reach, because it requires
#: the negation to sit ADJACENT to the token.
#:
#: ⛔ AND THE GAP ACCEPTS FUNCTION WORDS ONLY — a CONTENT word means the negation governs THAT
#: noun, not the token. The first version of this arm allowed any 40 characters, and a fresh
#: review measured the consequence: `Not a single test covers the P1.` and `Nothing was ever done
#: about the P1.` both closed an open `[P1]` and printed `READY-FOR-OWNER` — a REGRESSION against
#: both parents of the merge that introduced it, and the second time the file's own `:284-288`
#: rule (`no test for the P1` is a LIVE finding) had to be re-learned one arm over.
_SEVERITY_ABSOLUTE_NEGATION_BEFORE_RE = re.compile(
    r"(?i)\b(?:not\s+one|not\s+a\s+single|nothing|none\s+of|not\s+any\s+of)\b"
    r"(?:\s+(?:of|the|a|an|one|single|any|about|regarding|concerning|to|for|in|on))*\s*\Z"
)
#: ⛔ A UNIVERSAL-QUANTIFIER NEGATION IS NOT A CLEAR — IT SAYS AT LEAST ONE REMAINS.
#: `Not all of the P1 findings were fixed.` and `Not every P1 was addressed.` are the OPPOSITE of
#: a clear, and because the completion arm sees `findings were fixed` they used to clear an open P1
#: (a fresh review measured `check_blocking_finding` PASS on both). Unlike every other arm here,
#: this one is graded without requiring a completion: the sentence is decisive on its own, and a
#: `not all` that reaches a severity token can only be reading it as a live finding.
_SEVERITY_UNIVERSAL_NEGATION_BEFORE_RE = re.compile(
    r"(?i)\bnot\s+(?:all|every|each)\b[^.!?;\n]{0,40}\Z"
)
#: ⛔ A VERDICT ATTRIBUTED TO ANOTHER ARTIFACT IS A CITATION. The preamble is graded to the
#: START OF THE LINE — an arbitrary character cap bought nothing and cost a bypass: the same
#: `returned` verb stopped being in a 90-char window once a long clause sat between it and the
#: verdict. The verb class is grammatical (who REPORTED it), not an enumerated neighbourhood:
#: an earlier draft listed only `says?|returned|had been`, so `According to the review …`,
#: `The review's verdict …`, `stated`, `recorded` and `asserted` all cleared a live P1.
_ATTRIBUTED_BEFORE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:previous|earlier|prior|last|old)\s+(?:review|verdict|round|record|head|assessment)\b"
    r"|\b(?:review|round|record|verdict|assessment)'s\b"
    r"|\bthe\s+record\b"
    r"|^\s*(?:according\s+to|per|as\s+reported|per\s+the)\b"
    r"|\b(?:said|says?|stated?|recorded|asserted?|reported|wrote|written|noted|concluded"
    r"|returned|confirmed|quoted|cited)\b"
    r"|\bhad\s+been\b"
    r")"
)


def _retracted_from(text: str, end: int) -> bool:
    """True when what FOLLOWS ``end`` retracts, attributes or qualifies the clear.

    ⛔ TWO WINDOWS, ON PURPOSE. Rules (1)–(3) and rule (4)'s WORD arm are graded over the rest
    of the PARAGRAPH: a verdict on its own line followed by a line break is still one sentence
    on screen, so `**Verdict: CLEAN**\n— except the open P1`, `verdict: CLEAN\r\nno longer
    holds` and the blank-line form all cleared a live P1 under a line-scoped window. The BARE
    SEVERITY arm stays LINE-scoped and per-token, because the paragraph-wide form of it is what
    refuses the real `**Verdict: CLEAN.**\nFixed the P1 in 4f450ae81.` — the `P1` there is a
    SUBJECT, not a retraction. The docstring's earlier claim that the two trades were
    unavoidable was wrong; splitting the windows gives both.

    ⛔ AND THE BLANK-LINE FORM NEEDS ONE MORE STEP, or the two windows contradict each other
    (review cycle 5, finding 1; widened in cycle 6): `para_end = text.find("\n\n", end)` LANDS
    ON `end` when the verdict is followed by a blank line, so `para` was `""` and every rule
    below was skipped — `**Verdict: CLEAN**\n\nthe P1 is still open.` CLEARED while the
    single-newline spelling of the same sentence BLOCKED, one character apart, and the
    paragraph window claimed credit for closing it.

    ⛔ SO THE SUFFIX IS GRADED IN TWO LAYERS THAT OVERLAP BY DESIGN, AND NEITHER IS A "WINDOW
    OF N CHARACTERS". Two earlier revisions of this function were exactly that, and each one
    produced a cliff: a fix that crossed the blank line only for a STANDALONE verdict left
    every shared-line spelling open (cycle 6), and a fix that graded the severity arm per LINE
    and the word rules per PARAGRAPH left a live `P1` invisible on the line after the verdict
    while the same sentence one paragraph down blocked (cycle 7, finding 3 — an inverted
    gradient). The layers are:

      • THE VERDICT'S OWN PARAGRAPH — rules (1)–(4) in FULL, including rule (3)'s exception
        words. `except` / `apart from` / `besides` / `unless` describe the verdict when they
        sit beside it; `**Verdict: CLEAN.** No blocking findings; the P1 remains open.`
      • THE PARAGRAPH AFTER IT — rules (1) and (2). Rule (3) is deliberately NOT carried here:
        detached, those words describe ordinary prose (`no other files changed except the
        fixture`) and carrying them across refused real clears (#10962). Rule (4)'s WORD arm is
        also not carried here — it is graded over the whole suffix instead, because it is a
        closed state vocabulary and windowing it was a measured fail-open.
      • THE WHOLE SUFFIX — rule (4)'s WORD arm (`unfixed`, `still open`, `remains open`, …), the
        finding-state PHRASES (`no longer holds` / `no longer the case`), and the per-token
        severity arm (P0–P2 / blocker) — and NOTHING else. Windowed, the severity arm would be
        the cliff older revisions kept rebuilding; unbounded, it cannot be missed, because a
        reviewer who means to leave a finding open names it.

    A "paragraph" boundary is a whitespace-only line, not merely `\n\n` — see `_BLANK_LINE_RE`.
    """
    suffix = text[end:]
    blank = _BLANK_LINE_RE.search(text, end)
    own_para = text[end : blank.start() + 1 if blank else len(text)]
    if _RETRACTION_NO_SEVERITY_RE.search(own_para):
        return True
    if _SUFFIX_RETRACTION_RE.search(suffix):
        return True
    if blank:
        after = blank.end()
        nxt = _BLANK_LINE_RE.search(text, after)
        next_para = text[after : nxt.start() + 1 if nxt else len(text)]
        if _NEXT_PARA_RETRACTION_RE.search(next_para):
            return True
    # The severity arm is graded over the WHOLE suffix, so where the token sits makes no
    # difference: on the verdict's line, on the next line, one paragraph down, or four.
    # ⛔ THE CLEAR EXEMPTIONS ARE ANCHORED TO THE TOKEN, not searched in a character window.
    # `ever a completion sixty characters away stood the arm down, so `The P1 was never fixed.`
    # and `The P1 is still not closed.` both CLEARED (review cycle 8, P0) — a character window
    # is the bug, whichever distance it happens to use.
    for token in _SEVERITY_TOKEN_RE.finditer(suffix):
        left = suffix[: token.start()]
        # ⛔ A NEGATION THAT REACHES A COMPLETION VERB NEGATES THE COMPLETION, NOT THE FINDING.
        # `no|without|zero|none of` before the token reads as ABSENCE (`No P1 remains.`), which
        # is why the arm exists. But the same negation also spells a completion that did NOT
        # happen — `without the P1 being fixed`, `None of the P1 findings were addressed` — and
        # the token then names a LIVE finding. Exempting on the left window alone let those
        # clear, and so did the COMPLETED_AFTER arm below, which reads `findings were addressed`
        # as a completion without asking who negates it: measured at the pre-fix head,
        # `**Verdict: CLEAN** — without the P1 being fixed.` and
        # `**Verdict: CLEAN.** None of the P1 findings were addressed.` both yielded
        # `asserts_a_clear=True` / `_grades_as_blocking=False`, and `evaluate()` printed
        # `READY-FOR-OWNER` over an open P1 — the false PASS this module exists to prevent,
        # since the owner does not review. So the combination is DECISIVE, not a `continue`:
        # it must outrank the completion arm below, which is what a plain `continue` allowed.
        # The completion is graded ANCHORED at the token (a window is the bug this file has
        # re-learned four times), and only a COMPLETION verb counts: a liveness predicate after
        # the token (`remains`, `is open`, `needs work`) is itself negated by the `no`, so
        # `No P1 remains.` still clears.
        # ⛔ A UNIVERSAL-QUANTIFIER NEGATION IS DECISIVE ON ITS OWN (see the regex note):
        # `Not all of the P1 findings were fixed.` asserts at least one is still open.
        if _SEVERITY_UNIVERSAL_NEGATION_BEFORE_RE.search(left):
            return True
        if _SEVERITY_NEGATED_BEFORE_RE.search(left) or _SEVERITY_ABSOLUTE_NEGATION_BEFORE_RE.search(
            left
        ):
            if _SEVERITY_COMPLETION_NEGATED_AFTER_RE.match(suffix, token.end()):
                return True
            continue
        if _SEVERITY_ZERO_COUNT_RE.match(suffix, token.end()):
            continue
        if _SEVERITY_COMPLETED_BEFORE_RE.search(left) and not _SEVERITY_PREDICATE_AFTER_RE.match(
            suffix, token.end()
        ):
            continue
        if _SEVERITY_COMPLETED_AFTER_RE.match(suffix, token.end()):
            continue
        return True
    return False


def _attributed_before(text: str, start: int, end: int) -> bool:
    """True when the SAME LINE up to ``end`` attributes the verdict to another artifact.

    The window ends at the MATCH'S END, not its start: `CLEAR_VERDICT_RE`'s cue arm matches
    from the cue word, so `Previous review: CLEAN.` starts matching at `review` — a window
    ending at `start` sees only `Previous ` and the attribution word is the very next token.
    It also starts at the LINE START, with no character cap (see `_ATTRIBUTED_BEFORE_RE`).
    """
    line_start = text.rfind("\n", 0, start) + 1
    return bool(_ATTRIBUTED_BEFORE_RE.search(text[line_start:end]))


def asserts_a_clear(text: str) -> bool:
    """True when ``text`` ASSERTS a clean verdict — not when it cites, negates or supersedes one.

    Every match is checked, not just the first: a body may cite a retracted verdict in one
    clause and assert its own in another, and the assertion is what counts. Both directions are
    graded — the suffix (a retraction) and the same-line preamble (an attribution) — because a
    citation can carry neither, one, or both.

    ⛔ THE SUFFIX IS GRADED IN TWO OVERLAPPING LAYERS, NOT IN WINDOWS OF N CHARACTERS
    (see `_retracted_from`). Two earlier revisions of this function WERE windows, and each
    produced a cliff — one that crossed a blank line only for a standalone verdict, and one
    that graded the severity arm per LINE while the word rules went per PARAGRAPH, so a live
    `P1` on the line after the verdict was invisible while the same sentence one paragraph
    down blocked. The layers are:

      • the verdict's OWN PARAGRAPH — rules (1)–(4) in full, including rule (3)'s exception
        words, which are anchored to the verdict there;
      • the REST OF THE BODY, TO ITS END — rules (1), (2), (4)'s WORD arm, and the per-token
        severity arm.

    A line break therefore does not hide a retraction that NAMES a finding, at any distance:
    a severity token blocks wherever it sits unless a negation governs it or a completion sits
    on its left or right. `**Verdict: CLEAN.**\nFixed the P1 in 4f450ae81.` stays a clear, and
    so does `I fixed the P1 and the P2 in …` — the completion reaches the second token.
    `P1s` is a token too: the plural is what a review summary writes.

    ⛔ TWO KNOWN RESIDUALS, EACH DELIBERATE AND EACH PINNED in
    `tests/test_ready_for_owner.py`. They are listed HERE, on the artifact a fixer reads, and
    not in the commit message that introduced them — a residual that lives only in a commit
    message is invisible to the next reader, which is how the rule-(3) trade was re-derived as
    a fresh finding one cycle after it was decided.

      (1) rule (3)'s EXCEPTION WORDS do not cross a blank line. Detached there they are
          ordinary English — `no other files changed except the fixture` retracts nothing — so
          `**Verdict: CLEAN.**\n\nExcept the concern raised in cycle 4.` still clears. That is
          a measured trade, not an oversight: carrying rule (3) across refused real clears
          (that same `… except the fixture` sentence, the #10962 false-NOT-READY direction with
          measured history). A retraction that NAMES a finding is caught at any distance; only
          ones that name nothing can hide behind a blank line.

      (2) rule (4)'s WORD arm requires the word `open` (`remains open` / `still open`), so a
          live finding written WITHOUT a severity token and without `open` is invisible to
          BOTH arms — `**Verdict: CLEAN.** A critical finding remains.` reads
          `asserts_a_clear → True` and `_grades_as_blocking → False`, i.e. it CLOSES whatever
          was open. Tracked with the sibling corpus gaps on #11048.
          ⛔ THIS ONE IS NOT A CHEAP PATCH, and that is why it is recorded rather than fixed
          here: the word arm has NO negation scope, so the obvious `|remains?\b` addition
          blocks `No issues remain.` and `Nothing remains.` — the permanent false NOT-READY
          class whose measured history (#10962) is the reason this module widens the positive
          vocabulary at all. A correct fix is a NEGATION-GOVERNED word arm, the same shape as
          `_SEVERITY_NEGATED_BEFORE_RE`; that is design work, not a ninth patch inside a fold
          PR whose subject is five unrelated harness tools.

    Two earlier revisions of this docstring claimed the rule-(3) family was closed outright,
    which was false — hence this paragraph.
    """
    for m in CLEAR_VERDICT_RE.finditer(text):
        if _retracted_from(text, m.end()):
            continue
        if _attributed_before(text, m.start(), m.end()):
            continue
        return True
    return False


#: A CAPPED / ABORTED / DECLARED-NEGATIVE review exit. ⛔ THE VOCABULARY IS COPIED FROM
#: `scripts/record-review.sh`, NOT INVENTED HERE: it is that script's arms (1) and (1b) —
#: the two it applies UNCONDITIONALLY when deciding whether a review artifact is evidence
#: of a clean outcome — and arm (2), its loose `not[ _-]*clean` search WITH that script's
#: own `Outcome: clean` suppression. All three are applied here in the same order, so there
#: is ONE definition of "not clean" on this box, not a second weaker one.
NON_CLEAN_RE = re.compile(
    r"(?i)(?:auto-fix[ \t]+(?:stalled|failed|reached|aborted|converged|stuck)"
    r"|require[s]?[ \t]+human[ \t]+(?:attention|input|review)"
    r"|unresolved[ \t]+issues?"
    r"|[0-9]+[ \t]+issues?[ \t]+remain"
    r"|test[ \t]+review[ \t]+capped"
    r"|\[ADVERSARIAL-BOUND\]"
    r"|\[NO-LOOP-BUDGET\]"
    r"|(?:^|[*\s])outcome:[ \t]*[*\s]*(?:not|non|no)[ \t_-]*clean"
    r"|(?:^|[\s>*-])review[ \t]*[\u2014\u2013-][ \t]*not[ \t_-]*clean)"
)

#: The recorder's ARM (2) — the loose `not…clean` substring — and the SUPPRESSION that makes
#: it safe there: it does not fire when the artifact states an explicit `Outcome: clean`
#: (measured false refusal, #1964 / tortoise#8866: a genuine CLEAN review whose body used the
#: phrase later in a sentence was refused). Applied as a PAIR, exactly as the recorder does.
LOOSE_NEGATIVE_RE = re.compile(r"(?i)not[ \t_-]*clean")
EXPLICIT_CLEAN_OUTCOME_RE = re.compile(r"(?i)(?:^|[*\s])outcome:[ \t]*[*\s]*clean\b")

#: ⛔ A REVIEWER'S FINDING IN THE FLEET'S REAL SHAPE. The `[P1] file:line —` form above is
#: only one of three shapes actually posted, and the OTHER TWO WERE INVISIBLE, which made
#: this condition report PASS on PRs with an open P1/P2 (measured on #10726: two records
#: declaring an open P2 after the last clear; 25 of 203 real review bodies across five PRs
#: graded NEUTRAL). The shapes, each anchored to a source rather than guessed:
#:
#: * `severity: P1` — the `ISSUE:` block field, defined by
#:   `skills/code-review/SKILL.md` §"ISSUE: block" (its `severity:` line IS the finding's
#:   declared severity, indented two spaces under `ISSUE:`).
#: * `Outcome: 2 findings (1 × P1, 1 × P2)` / `Outcome: 3 issues — 2 blocking …` — the
#:   reviewer's own verdict HEADER, which is how a merged review states its count.
#: * `Verdict: ISSUES 1` — the same verdict stated mid-line rather than at column 0.
#:
#: ⛔ THE `severity` ALTERNATIVE IS UNANCHORED, like its two siblings. It was line-anchored,
#: and the COMPOSED forms reviewers actually post in this repo (`**severity: P3 ·
#: check_type: … · origin: diff**`, `- severity: P1`, `**Severity:** P1`, `> severity: P2`)
#: then read NEUTRAL — a false PASS, the #10726 class this whole arm exists to close.
FINDINGS_DECLARED_RE = re.compile(
    r"(?i)(?:\bseverity\b\**\s*:?\s*\**\s*(P0|P1|P2)\b"
    r"|\boutcome\s*:\s*\**\s*([0-9]+)\s+(?:issues?|findings?)\b"
    r"|\bverdict\s*:\s*\**\s*([0-9]+)\s+(?:P[0-3]\s+)?(?:issues?|findings?)\b"
    r"|\bverdict\s*:\s*\**\s*ISSUES\s+([0-9]+)\b)"
)

#: A body that declares a COUNT must also declare a BLOCKING severity for the count to
#: block: `**Outcome: 3 issues (0 blocking).**` and `2 findings (both P3), no blocking
#: defect` are CLEAN reviews of a P3-only round, and reading the count alone blocked four
#: such bodies measured across ten PRs — a permanent false NOT-READY, and a contradiction of
#: this gate's own rule that a `[P3]` note does not block.
ANY_SEVERITY_TOKEN_RE = re.compile(r"(?i)\bP([0-3])s?\b")
#: ⛔ `no blocking` only counts as a DECLARATION when it ENDS there — "No blocking other
#: than the P1" is the opposite claim, and matching it was the round-7→8 false PASS.
ZERO_BLOCKING_RE = re.compile(
    r"(?i)\b0\s+blocking\b"
    r"|\bno\s+blocking\b(?=\s*(?:[.,;:!)\]]|$|\s+(?:findings?|defects?|issues?|problems?)\b))"
)
#: The ISSUE block's SIBLING fields. A real finding always carries at least one of these
#: next to its severity; bare prose that happens to say "severity: P1" does not. Used to
#: tell a DECLARATION from a sentence ABOUT a past finding — round 8 measured 5 of 6 natural
#: CLEAN phrasings blocking without this discriminator.
#:
#: ⛔ `outcome:` is NOT a sibling: it is the VERDICT header, so treating it as one made
#: `**Outcome: CLEAN.** The one severity: P2 note from round 2 is fixed` block. The ISSUE
#: block's own `outcome:` field is covered by the line-start arm, because the block's
#: `severity:` is on its own line.
SIBLING_FIELD_RE = re.compile(
    r"(?i)(?:\bcheck_type\s*:"
    r"|\borigin\s*:\s*(?:diff|history|context|base|design)\b"
    r"|\b(?:location|file)\s*:\s*\S"
    r"|\b(?:description|suggestion)\s*:)"
)
#: A severity declaration in FIELD position: its own line (the `ISSUE:` block's indented
#: `severity: P1`), optionally bulleted, bolded, quoted or backticked.
SEVERITY_LINE_START_RE = re.compile(
    r"(?im)^\s*(?:[-*>]\s*)*\W{0,2}`?severity`?\s*\**\s*:?\s*\**\s*P[0-2]\b"
)

#: ⛔ THE VERDICT-HEADER SHAPES AN EARLIER ROUND STILL MISSED — all measured on live bodies:
#: `**Outcome: ISSUES 3** — 1 code defect (security), 2 false statements` (#10956), `##
#: Code review — ISSUES FOUND (1 × P1, 1 × P2)` (#10929), `## Code review — ISSUES FOUND (1 ×
#: P2)` (#10920), `### ⛔ P1, verified by reproduction` (#10938), `Do not land this PR as it
#: stands — it carries a known P1` (#10945). Each read NEUTRAL, and each as a PR's LAST
#: artifact printed READY-FOR-OWNER.
ISSUES_FOUND_RE = re.compile(r"(?i)(?<!no )\bissues?\s+found\b|\bISSUES\s+\d+\b")
#: ⛔ A severity TOKEN is a finding by POSITION too, not only by decoration. Live shapes
#: that still read NEUTRAL one round after the header fix: `P2:` followed by a
#: `` `path:line` `` (the `${review-pr.sh}` USE cue), and a tally that counts a NON-ZERO
#: blocking severity (`- P1: 0, P2: 2`). A finding NAMES A LOCATION.
SEVERITY_LOCATED_RE = re.compile(r"(?i)\b" + SEV + r"\b[^.\n]{0,60}?`?[\w./-]*\.\w+:\d+")
TALLY_NONZERO_RE = re.compile(r"(?i)\bP([0-2])s?\s*:\s*([1-9]\d*)\b")
#: ⛔ PARITY WITH `scripts/review-pr.sh`'s OWN CUE LIST — the *label-noun-in-front* cue and
#: the *table-row* cue, which this module did not have. Round 12 measured ten live shapes
#: that declare a blocking finding and read NEUTRAL because every arm needed a line-lead, a
#: `path.ext:12`, a sibling field, a heading or a tally: `**Verdict: P1 — …**`,
#: `**Finding [P1]**: …`, `**Verdict: ISSUES — 1 blocking (P1), 1 non-blocking (P2).**`,
#: `There is a **P1** in the guard`, `**1 blocking (P1)**: …`, `Blocking finding: P1 …`,
#: `Requires a fix: P1 …`, `| **P1** | the guard … |` (no location), and
#: `2 [P1] open blocking defects remain`. `review-pr.sh:1204/1221/1840` are those cues.
#: The label-noun cue is TIGHT on purpose: the noun must be a KEY (followed by a colon) and
#: the severity must follow within 30 characters, with no `clean` in between — otherwise
#: ordinary CLEAN prose ("No blocking findings. The severity: P2 bar …") would block, which
#: is the false-BLOCK direction 5 of 6 natural phrasings fell into in round 8.
LABEL_NOUN_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:verdict|outcome|finding|findings|issue|issues|defect|defects|blocker|blockers)"
    # The gap must not cross a closing bracket or a sentence end: a count declaration's
    # own "(0 blocking)." must not carry its severity into the NEXT sentence.
    r"\s*\**\s*:(?:(?!clean)[^.\n)]){0,30}?\bP[0-2]\b"
    r"|\b(?:finding|issue|defect)s?\s*[\[(]P[0-2][\])]"
    # ⛔ THE SLOT-SEPARATOR CUE (`review-pr.sh` arm (b2), added there as a MEASURED P1):
    # a bare severity token followed by a separator and a clause — `Found a P1: the guard
    # returns 0`, `One P1 — the guard …`. A plain-English blocker with no location is the
    # commonest shape a lane posts as a follow-up comment.
    r"|\bP[0-2]\b\s*(?:[:\u2014\u2013]\s+(?!(?:0|none|nil|zero)\b)\S"
    r"|\s+[\u2014\u2013]\s+(?!(?:0|none|nil|zero)\b)\S)"
    r"|\bblocking\s+(?:finding|issue|defect)s?\s*:\s*P[0-2]\b"
    # A BOLD severity token used as a noun mid-sentence (`There is a **P1** in the guard`),
    # unless it is a zero count (`**P1: 0**`, `**P1**: none.`).
    r"|\*\*P[0-2]\*\*(?!\s*[:|]?\s*(?:0|none|nil|zero)\b)"
    # A LABEL followed by a parenthesised severity — `- **F1 (P2)**`, `Finding (P1)`.
    r"|\b[A-Za-z]\w{0,10}\s*\((P0|P1|P2)\)"
    # `Requires a fix: P1 …` / `Needs a P2 …`
    r"|\b(?:requires?|needs?)\b[^\n]{0,25}?\bP[0-2]\b"
    r")"
)
#: `1 blocking (P1)` / `2 blocking findings` — a stated NON-ZERO blocking count.
BLOCKING_COUNT_RE = re.compile(r"(?i)(?<!no )\b[1-9]\d*[ \t]+blocking\b")
#: ⛔ A COUNT WRITTEN BEFORE THE TOKEN, or spelled as a numeral — `review-pr.sh`'s
#: `COUNT_SCAN_AWK` (:1761-1929) and arm (b4) flag both, and both are recorded there as
#: MEASURED fail-opens ("`CLEAN — 2 P1 findings.` produced NO blocking line and a `clean`
#: record"). `CLEAN — 2 P1 findings.` matches CLEAR_RE, so without this the body reads CLEAR.
#: `<numeral> [≤3 words] <finding noun>` — `review-pr.sh`'s `COUNT_SCAN_AWK` forward shape.
#: `4 defects remain unresolved`, `5 errors remain unfixed`, `3 issues outstanding` all read
#: NEUTRAL without it, one noun substitution away from the `2 P1 findings` form that was fixed.
COUNTED_FINDING_RE = re.compile(
    r"(?i)(?<![\w-])(?!"
    + NUMERAL
    + r"0\b)"
    + NUMERAL
    + r"(?:\s+\w+){0,3}\s+"
    + FINDING_NOUN
    + r"\b"
)
COUNT_BEFORE_TOKEN_RE = re.compile(
    # DIGITS only for the count-before-token arm: a spelled numeral is an ARTICLE as often
    # as a count ("One P2 was fixed" is not a finding), and the spelled form that DOES
    # count is `one blocking finding`, covered below.
    r"(?i)(?<![\w-])(?!"
    + NUMERAL
    + r"0\b)"
    + r"(?:[2-9]|[1-9][0-9]+|two|three|four|five|six|seven|eight|nine|ten|several|multiple)"
    + r"(?:\s+\w+){0,3}\s+"
    + SEV
    + r"\b"
    r"|\b" + NUMERAL + r"\s+blocking\s+" + FINDING_NOUN + r"\b"
)
#: ⛔ THE COUNT IS `1` — the COMMONEST open-blocker count, and the one the class above cannot
#: take. `COUNT_BEFORE_TOKEN_RE` starts its digits at 2 and its spelled numerals at `two`,
#: because a spelled `one` is an ARTICLE as often as a count (`One P2 was fixed` is not a
#: finding). Omitting the count entirely flipped the polarity on the DIGIT alone, measured on
#: this module: `'CLEAN — 2 remaining P1'` blocked, `'CLEAN — 1 remaining P1'` did not.
#: Two reviewers filed it independently (rounds 21 and 22) because the strongest wrong answer
#: follows — `CLEAR_RE` matches that body too, so a comment whose only blocking statement is
#: "1 remaining P1" was graded a CLEAR and the owner's tab said READY-FOR-OWNER for a PR
#: carrying an open P1.
#: The article guard is kept by requiring a PRESENCE CUE, not by dropping the count: the cue
#: may sit between count and token (`1 remaining P1`, `one open P1`, `1 unresolved P1`,
#: `1 more P1`) or after it (`1 P1 open`, `1 P1 remains`). `One P2 was fixed` has neither, so
#: it still reads NEUTRAL — the false-BLOCK direction round 8 measured 5 of 6 phrasings into.
_ONE_OPEN_CUE = (
    r"(?:open|opened|opening|remaining|remains?|outstanding|unresolved|still|left|more|pending)"
)
ONE_OPEN_COUNT_RE = re.compile(
    r"(?i)(?<![\w-])(?:1|one)\b(?:\s+\w+){0,2}\s+"
    + _ONE_OPEN_CUE
    + r"\b(?:\s+\w+){0,2}\s+"
    + SEV
    + r"\b"
    r"|\b(?:1|one)\b(?:\s+\w+){0,2}\s+" + SEV + r"\b(?:\s+\w+){0,2}\s+" + _ONE_OPEN_CUE + r"\b"
)
#: ⛔ THE CUE-BEFORE-COUNT SPELLING — the one a reviewer writes when SUMMARISING, and the one
#: the two arms above cannot take: they require the cue BETWEEN count and token or AFTER the
#: token. Measured at `858191e28`: `CLEAN — 1 remaining P1` blocked, while
#: `CLEAN — remaining: 1 P1 (open)` did NOT — the same polarity flip the count-`1` fix closed,
#: reached by moving the cue one word to the left. `NO ISSUES FOUND except 1 P1.` reaches it
#: without a `CLEAN` prefix at all, because `CLEAR_RE` matches the bare phrase and wins.
#: The EXCLUSION cues (`apart from` / `except` / `other than` / `aside from` / `besides`) carry
#: the same polarity — they name the finding as the exception to an otherwise clean result — and
#: `single` / `a single` are count words for the commonest one-item summary.
#: The article guard round 8 measured is kept in the OTHER direction: a completing verb after the
#: token (`remaining: 1 P1 was fixed`) is a CLOSED finding, not an open one, and must stay
#: certifiable — the same rule `_fixed_after_token` encodes for the sibling arms.
_ONE_OPEN_CUE_BEFORE = (
    _ONE_OPEN_CUE + r"|apart\s+from|except(?:ing|ed)?(?:\s+for)?|other\s+than|aside\s+from|besides"
)
ONE_OPEN_CUE_BEFORE_COUNT_RE = re.compile(
    r"(?i)(?<![\w-])(?:"
    + _ONE_OPEN_CUE_BEFORE
    + r")\b[\s:=\u2014\u2013-]{0,4}"
    + r"(?:a\s+)?(?:1|one|single)\b(?:\s+\w+){0,2}\s+"
    + SEV
    + r"\b"
    # ⛔ THE CLOSED DIRECTION: a completing verb after the token means the finding was FIXED.
    # `remaining: 1 P1 was fixed`, `apart from a single P2, now closed` — the same declarative
    # form with or without a copula, and the false-BLOCK direction round 8 measured. The verb
    # itself is NOT optional in this lookahead, so a live `1 P1 (open)` is never exempted.
    # ⛔ THE VERB SET IS NOT INTERCHANGEABLE. `fixed|closed|resolved|addressed` are completing on
    # their own, but `landed`, `cleared` and `done` are not unambiguous here: in this fleet
    # "landed" means MERGED / ARRIVED, so `remaining: 1 P1 landed after the last review and is
    # unfixed` is a LIVE finding, and the bare form exempted it — the same fail-open direction,
    # one spelling over. Those three therefore require a copula or `now`; the unambiguous four
    # still do not, so `apart from a single P2, now closed` keeps working.
    + r"(?!\s*(?:,\s*)?(?:now\s+)?"
    + r"(?:"
    + r"(?:(?:was|were|is|are|has|have|had)\s+(?:now\s+)?)?(?:fixed|closed|resolved|addressed)"
    + r"|(?:was|were|is|are|has|have|had)\s+(?:now\s+)?(?:landed|cleared|done)"
    + r"))"
)
#: A bracketed severity token used as a NOUN in running text (`2 [P1] open blocking defects`).
BRACKET_SEVERITY_RE = re.compile(
    r"(?i)\[P[0-2]\]\s*(?:open|blocking|defects?|issues?|findings?|remain|unresolved)"
)
#: A TABLE ROW whose severity cell is the token (`| **P1** | the guard returns 0 |`).
#: A table row blocks only when it carries a DESCRIPTION cell — `review-pr.sh`'s
#: table-scaffold rule (:1565): `| **P1** | 0 |`, `| P0 | P1 | P2 |` and `| 0 | 0 | 2 |` are a
#: reviewer's own tally and must not block.
TABLE_SEVERITY_ROW_RE = re.compile(r"(?im)^\s*\|\s*\**" + SEV + r"\**\s*\|\s*([^|\n]*)\|")


def _table_finding(text: str) -> bool:
    """A table row whose severity cell is followed by a DESCRIPTION or a NON-ZERO count.

    A two-column row (`| P1 | the guard returns 0 |`, `| P1 | 2 |`) is the commonest shape;
    `| **P1** | 0 |` and `| 0 | 0 | 2 |` are a reviewer's own tally and are not findings.
    Done in code rather than in one regex: a negation lookahead after whitespace is fragile
    enough that it silently matched the tally rows it was written to exempt.
    """
    for match in TABLE_SEVERITY_ROW_RE.finditer(text):
        cell = match.group(1).strip()
        if re.fullmatch(r"(?i)(?:0|none|nil|zero)", cell):
            continue
        # `| P0 | P1 | P2 |` — every cell is a bare severity token: a tally, not a finding.
        if re.fullmatch(r"(?i)\**P[0-3]\**", cell):
            continue
        return True
    return False


#: A severity COUNT of zero in any spelling — `P1: 0`, `**P1**: none.`, `P1: nil`, `P1: zero`.
#: `review-pr.sh:1221-1224` exempts exactly these ("`**P0**: none.` is a count").
ZERO_SEVERITY_COUNT_RE = re.compile(r"(?i)\b" + SEV + r"\b\**\s*:\s*\**\s*(?:0|none|nil|zero)\b")

#: A severity named in a HEADING, or a plain "do not land / known P1" verdict.
SEVERITY_HEADING_RE = re.compile(
    r"(?im)(?:^\s*#{1,6}[^\n]*\bP[0-2]\b"
    r"|\bdo\s+not\s+land\b"
    r"|\bknown\s+P[0-2]\b)"
)

#: A FORMAL change request is a blocking record even when the review body is empty — the
#: findings may live entirely in inline comments, and an unresolved `CHANGES_REQUESTED`
#: is GitHub's own record of "not clean yet".
CHANGES_REQUESTED = "CHANGES_REQUESTED"
APPROVED = "APPROVED"
DISMISSED = "DISMISSED"

#: A PR that DECLARES it reverses a recorded decision. The `OVERRIDES:` line (AGENTS.md
#: USER QUESTIONS PROTOCOL) is what such a departure must carry; without it the reversal
#: is awaiting an owner decision and must not be offered as a merge.
REVERSAL_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:REVERSES|REVERSAL|REVERSING)\b[^\n]*"
    r"|reverses a recorded decision"
)
OVERRIDES_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?\**OVERRIDES\**\s*:")

#: The declared-union marker a fold/batch PR writes in its body, e.g.
#:     READY-GATE UNION: #10901, #10907
#:     READY-GATE UNION: tortoise/sdk.py, tools/foo.py
#: ⛔ `(.*)`, NOT `(.+)`: a marker with nothing after the colon (`READY-GATE UNION:`) is
#: a MALFORMED DECLARATION, and `(.+)` simply failed to match it — which sent it down
#: the "no union declared" branch and PASSED. `(.*)` makes it a declared-but-empty
#: union, which `check_coverage` fails. `\s*` before the group is deliberate: it lets
#: trailing whitespace only count as empty rather than as one blank entry.
UNION_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?\**READY-GATE UNION\**\s*:[ \t]*(.*)$")
PR_REF_RE = re.compile(r"^#?(\d+)$")

REVIEW_OK_TOKENS = frozenset({"head", "carry"})
#: The verdicts `record-review.sh` will mint (its own guard, `:1303`). A record carrying
#: anything else — `not-clean` above all — is an explicit statement that the PR is NOT
#: ready, however the `--check-carry` token reads.
CLEAN_REVIEW_VERDICTS = frozenset({"clean", "clean-micro", "clean-low"})


def _env_int(name: str, default: int) -> int:
    """A bounded knob from the environment, parsed WITHOUT risking the verdict contract.

    ⛔ Parsed at module import, `int(os.environ.get(...))` raised ``ValueError`` before
    ``main()`` — and ``main()``'s ``except Exception`` backstop (the OUTPUT CONTRACT, see
    ``:1984``: "A read that is not a verdict must never become a traceback with NO line")
    lives INSIDE ``main()``, so it cannot see a module-level raise. Measured with the
    previous shape:

        READY_GATE_GH_TIMEOUT=1m uv run python tools/ready_for_owner.py 1
          -> ValueError: invalid literal for int() with base 10: '1m'
          -> exit 1 with NO verdict line on stdout

    A named warning on stderr plus the documented default keeps the contract: the run still
    produces exactly one verdict line, and a bad knob is never silently reinterpreted.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        print(
            f"ready-gate: {name}={raw!r} is not an integer — using the default {default}",
            file=sys.stderr,
        )
        return default


def _tri_bool(value: Any) -> bool | None:
    """A tri-state field as `bool | None`, refusing to INVENT a boolean.

    ⛔ `bool("false")` is `True`. Coercing an uncoerced `--fixture` value with `bool()` is a
    FAIL-OPEN for any field graded by `is False` / `is True`: measured on `head_stable`,

        'false'    -> bool() -> True   -> passed=True  reason=''   ← says head did NOT stay put
        'unstable' -> bool() -> True   -> passed=True  reason=''

    Only a real `bool` is evidence; anything else is "not read", and every consumer of that
    `None` below fails CLOSED (`exclusions` reports `unreadable`). The live collectors already
    return `bool | None`; this is the `--fixture` door.
    """
    if isinstance(value, bool):
        return value
    return None


GH_TIMEOUT = _env_int("READY_GATE_GH_TIMEOUT", 60)
GIT_TIMEOUT = _env_int("READY_GATE_GIT_TIMEOUT", 180)


class ReadError(RuntimeError):
    """An input could not be read. NEVER a verdict, and never green."""


class RequiredConfigReadError(ReadError):
    """The required set was not derivable because the repo's CONTRACT is wrong.

    A subclass of `ReadError` so every existing handler still REFUSES, and its own class
    so the distinction survives the read boundary: `tools.required_set.RequiredSetConfigError`
    says the repo's two sources of truth disagree (a `check-success=` line dropped while
    the committed record still names the context, or a record whose own `live_result` is
    `DIVERGED`) — the #3016 regression this module exists to catch. That is a VERDICT
    about configuration, never an I/O failure: `FAIL(unreadable)` means "could not be
    read, RETRY" in this module's vocabulary, and retrying will not change a config.

    ⛔ The `RequiredSetConfigError` docstring states the same rule from the other side:
    *"What must never happen is that this class is caught and treated as a retryable read
    failure."* Collapsing it into `ReadError` at `_read_required_contexts` is exactly that
    collapse, one frame later.
    """


class RateLimited(ReadError):
    """GitHub refused the read for rate-limit reasons (403/429)."""

    def __init__(self, endpoint: str, detail: str = "") -> None:
        super().__init__(f"GH-RATE-LIMITED({endpoint})" + (f": {detail}" if detail else ""))
        self.endpoint = endpoint


# ── the verdict of one condition ────────────────────────────────────────────


@dataclass(frozen=True)
class Check:
    """One condition's verdict, carrying the RAW value that produced it."""

    name: str
    passed: bool
    raw: str
    reason: str = ""
    unreadable: bool = False

    @property
    def status(self) -> str:
        if self.passed:
            return "PASS"
        return "FAIL(unreadable)" if self.unreadable else "FAIL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.name,
            "status": self.status,
            "passed": self.passed,
            "reason": self.reason,
            "raw": self.raw,
            "unreadable": self.unreadable,
        }


@dataclass(frozen=True)
class Verdict:
    pr: int
    head: str
    checks: tuple[Check, ...]

    @property
    def ready(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.checks if not c.passed)

    def line(self) -> str:
        """The one machine-greppable line. READY only when EVERY condition passed."""
        if self.ready:
            return f"READY-FOR-OWNER({self.pr}, {self.head})"
        return f"NOT-READY({self.pr}, failed={','.join(self.failed)})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pr": self.pr,
            "head": self.head,
            "ready": self.ready,
            "failed": list(self.failed),
            "line": self.line(),
            "checks": [c.to_dict() for c in self.checks],
        }


# ── the raw inputs, exactly as read ─────────────────────────────────────────


@dataclass
class Probe:
    """Everything the six conditions read, already fetched.

    The split is deliberate: the IO layer builds a Probe, the six condition functions
    are PURE functions of it. That is what makes the red-first test matrix possible —
    a test mutates one field of a green fixture and asserts the exact failure, with no
    network, no clock and no git.
    """

    pr: int
    repo: str = DEFAULT_REPO
    head: str = ""
    base: str = "main"
    is_draft: bool | None = None
    mergeable: str = "UNKNOWN"
    merge_state: str = ""
    head_committed_at: str = ""
    body: str = ""
    #: ``True`` clean · ``False`` dirty · ``None`` unreadable.
    tree: bool | None = None
    tree_raw: str = ""
    #: Newest attempt per ``(app, workflow, job)`` — ``None`` = the surface was unreadable.
    groups: tuple[ci_verdict.GroupVerdict, ...] | None = None
    groups_raw: str = ""
    #: The WORKFLOW RUNS on this head (``actions/runs?head_sha=``), read by the same call
    #: that produced ``groups`` — ``None`` = unreadable. Carried because a merge-time
    #: context's ABSENCE has two causes and only the run listing separates them: a job
    #: with unmet ``needs`` has no check-run yet, so "not created YET" and "this workflow
    #: never ran here" look identical in ``groups``.
    head_runs: tuple[Mapping[str, Any], ...] | None = None
    #: Required ENTRY contexts — must be green at the PR head. ``None`` = unreadable.
    #: Derived from ``.mergify.yml``'s ``queue_conditions`` (see `tools.required_set`).
    required: tuple[str, ...] | None = None
    #: Required MERGE-TIME contexts (``merge_conditions``). Mergify settles them on
    #: ``mergify/merge-queue/<sha>``, so an author head that carries no run of the
    #: emitting workflow leaves them DEFERRED rather than missing. ⛔ DEFERRED is decided
    #: by that EVIDENCE, never by absence alone: re-measured 2026-11-05, ``python-ci-gate``
    #: DOES report on author heads (``queued``, ``success`` and ``failure`` all observed),
    #: and a ``needs``-gated job that has not started has no check-run at all — so
    #: absence is also what a PENDING aggregate looks like, which is a FAIL here.
    #: ``None`` = unreadable, and it is kept SEPARATE from ``required`` because
    #: conflating the two is the permanent false NOT-READY this split exists to end.
    required_deferred: tuple[str, ...] | None = None
    #: Set only when the required set was not derivable because the repo's CONTRACT is
    #: wrong (a ``RequiredSetConfigError``: the committed record and ``.mergify.yml``
    #: disagree, or the record's own ``live_result`` is not clean). ``check_gates`` turns
    #: it into a plain ``FAIL`` — never ``FAIL(unreadable)``, which means "could not be
    #: read, RETRY" and would point a lane at the wrong subsystem. Empty = no config error.
    required_config_error: str = ""
    #: ``merge-time context -> workflow FILES that emit it`` (the record's ``emitters``
    #: projection, via `tools.required_set.RequiredSet.emit_files`). An empty answer for a
    #: required name means the emission is UNATTRIBUTABLE, which fails the gate rather than
    #: falling through to DEFERRED.
    merge_emit_files: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: The workflow FILE names that EXIST in the tree the required set was derived from
    #: (`.github/workflows/*.yml` at the protected ref, via
    #: `tools.required_set.RequiredSet.workflow_files`). ``None`` = not read.
    #:
    #: ⛔ WHY THE PROJECTION ABOVE IS NOT ENOUGH ON ITS OWN (#11175): `emit_files` is a
    #: COMMITTED projection, rewritten only by `mergify_config_guard --recut` (a weekly
    #: cron) and by `--live`. Rename or delete the emitting workflow and land it on `main`
    #: without a recut, and the record still names the OLD file — which matches no run on
    #: any head, so "the emitter did not run here" (DEFERRED, a PASS) becomes
    #: indistinguishable from "the recorded spelling is stale" (#11175's false READY).
    #: Comparing the projection against the files that actually exist separates them.
    base_workflow_files: tuple[str, ...] | None = None
    required_raw: str = ""
    #: ``record-review.sh --check-carry`` verdict token and exit code.
    review_token: str | None = None
    review_exit: int | None = None
    review_raw: str = ""
    #: The RECORD's own `verdict` field (``clean`` / ``clean-micro`` / ``clean-low`` /
    #: ``not-clean`` …). ``None`` = could not be read.
    review_verdict: str | None = None
    #: The PR's changed files — ``None`` = unreadable.
    touched: tuple[str, ...] | None = None
    touched_raw: str = ""
    #: The declared union (from the PR body / ``--declared-union``) — ``None`` = none.
    union: tuple[str, ...] | None = None
    union_raw: str = ""
    #: A union WAS declared but could not be resolved. Distinct from ``union is None``
    #: (no declaration at all) — conflating them disables the hand-picked-subset arm.
    union_unreadable: bool = False
    #: Files for which the selection schedules NO test leg at all (``test_files`` empty).
    uncovered: tuple[str, ...] | None = None
    coverage_raw: str = ""
    #: PR comments + review bodies, chronological, each ``{id, created_at, kind, body}``.
    comments: tuple[Mapping[str, Any], ...] | None = None
    comments_raw: str = ""
    #: The PR's label names, as read. ``None`` = unreadable.
    labels: tuple[str, ...] | None = ()
    labels_raw: str = ""
    #: ``open``/``closed`` as GitHub states it, and whether it already merged.
    pr_state: str = "open"
    merged: bool | None = None
    #: Re-read AFTER every other read: ``True`` still the same head, ``False`` it moved
    #: mid-read, ``None`` the re-read was unreadable.
    head_stable: bool | None = None
    #: Set by tests/callers that need a fixed clock; default = now (UTC).
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


# ── condition 1 — tree ──────────────────────────────────────────────────────


def check_tree(probe: Probe) -> Check:
    """The tree that will actually exist — ``merge-tree``, never the ``mergeable`` flag.

    ``mergeable`` is GitHub's cached tree fact and it lags: #10790 read DIRTY after its
    fix had landed, #10962 read MERGEABLE with 10 of 11 checks dead. The flag is quoted
    in the raw value for the owner's benefit and is NEVER the decision.
    """
    flag = f"mergeable={probe.mergeable} mergeState={probe.merge_state or '-'}"
    if probe.tree is None:
        return Check("tree", False, probe.tree_raw or "-", "unreadable", unreadable=True)
    if probe.base != PROTECTED_BRANCH:
        # #10597: a merge into a non-default base lands the work on a sibling branch and
        # closes nothing. The tree that matters is `main ∪ head`; a PR based elsewhere is
        # not a candidate for a merge-from-the-UI at all.
        return Check(
            "tree",
            False,
            f"base={probe.base} head={probe.head[:12]} {flag}",
            f"base-not-main:{probe.base}",
        )
    if probe.tree is not True:
        # ⛔ `is not True`, NOT `not probe.tree`. `probe.tree` arrives UNCOERCED from a
        # `--fixture` payload (`:2062` `tree=data.get("tree")`), so a fixture whose value is
        # the STRING "dirty" is TRUTHY and `not probe.tree` graded it PASS — measured:
        #   tree='dirty' -> passed=True reason=''      (a value that literally says dirty)
        #   tree=1       -> passed=True reason=''
        # The live collector is safe (`_read_tree` returns `bool | None`), so this door is
        # the fixture one — but a PASS over a value that says "dirty" is exactly the
        # fail-open this check exists to prevent. `None` is graded unreadable just above, so
        # anything reaching here that is not the literal `True` is not evidence of a clean
        # tree. The CONFLICTING arm further down uses the same `is not True` shape.
        return Check("tree", False, probe.tree_raw or "dirty", "dirty")
    return Check("tree", True, f"{probe.tree_raw} {flag}".strip())


# ── condition 2 — gates ─────────────────────────────────────────────────────


def _gate_token(group: ci_verdict.GroupVerdict) -> str:
    """`''` when this group PASSES the readiness gate, else the fail token."""
    conclusion = (group.conclusion or "null").lower()
    if group.voided_red:
        # A measured red that no newer SUCCESS re-measured. A cancellation — or a
        # skipped/neutral re-run — cannot clear it (ci_verdict's rule, and the rail's).
        return "red-voided"
    if group.state == ci_verdict.GROUP_IN_FLIGHT:
        return "in-flight"
    if group.state == ci_verdict.GROUP_RED:
        return f"red:{conclusion}"
    if group.state == ci_verdict.GROUP_ABSENT:
        # cancelled / stale: measured NOTHING. Absence is not a pass.
        return f"absent:{conclusion}"
    if group.state != ci_verdict.GROUP_GREEN:
        # ⛔ WHITELIST, not a fall-through. A state this module has not seen used to reach
        # the conclusion check below and PASS on `conclusion=success` — the same
        # unrecognised-value-falls-through-the-gap defect the polarity rule forbids.
        return f"state:{group.state}"
    if conclusion != REQUIRED_CONCLUSION:
        # green for GitHub, not for us: skipped/neutral exercised no code.
        return f"non-measuring:{conclusion}"
    return ""


def _merge_time_absence(probe: Probe, context: str) -> tuple[str, str]:
    """Explain an ABSENT merge-time context: ``(failure token or "", seen note)``.

    A ``merge_conditions`` name is authoritative on the QUEUE branch, so an author head
    that carries no trace of it is genuinely DEFERRED. The trap is that the two causes of
    absence are invisible in `groups`:

      * the workflow never ran on this head — the check does not exist by design here;
      * the workflow DID run and this job's `needs` are not satisfied yet — GitHub has
        created **no check-run at all** for a dependent job, so a PENDING aggregate looks
        exactly like an absent one (vendor behaviour, `needs`, GitHub Actions workflow
        syntax).

    Those two need OPPOSITE verdicts, and this module's own rule decides which: *"In-flight
    is a FAIL here, not a wait: a head whose checks have not concluded is not ready now."*
    So a head that carries a PR-triggered run of the emitting workflow is a FAIL — the
    aggregate is pending, or its legs already concluded and it never posted — and only a
    head with no such run defers.

    ⛔ THE MEASUREMENT THAT DROVE THIS: `python-ci-gate` reports on author heads
    (`queued` 11097, `completed/success` 11100, `completed/failure` 11096), and head
    `92299555219e` of PR 11113 carried `test (g)` RED with the aggregate absent. An
    unconditional DEFERRED graded that head PASS over a red required suite.

    ⛔ UNATTRIBUTABLE IS A FAIL, NOT A DEFER. If the committed record carries no emitter
    for a required name, "did that workflow run here?" cannot be asked at all, and the
    DEFERRED arm would be a PASS built on no evidence. The remedy is the one the guard
    already documents for this projection: `tools/mergify_config_guard.py --recut`
    rewrites `emitters`.

    ⛔ AND THE PROJECTION ITSELF IS VALIDATED AGAINST THE PROTECTED REF (#11175). A
    recorded emitter that names NO workflow file present at the ref is a STALE SPELLING —
    a workflow was renamed/removed and landed without a re-cut — and "no run of that file
    exists on this head" is then true of EVERY head for a reason that says nothing about
    the aggregate. Matched against runs alone, that reads DEFERRED (a PASS) while the
    required heavy suite never ran: measured on the #11175 reproduction, the SAME head
    evidence (one `pull_request` run of `python-suite.yml`) reads PASS with the stale
    spelling `python-ci.yml` and FAIL `missing` with the real one. So the spelling is
    checked first, and a stale one is UNATTRIBUTABLE.

    ⛔ THE OVER-BLOCK DIRECTION IS DECIDED HERE, NOT DISCOVERED LATER (#11175 DoD). After
    such a rename this arm reports `NOT-READY(unattributable)` for EVERY PR, not just the
    ones touching the emitter, until a human re-cuts the record — that is a FLEET-WIDE
    refusal, and it is the safe direction deliberately: the alternative (deferring on a
    spelling that matches nothing) is a false READY, which this module may never produce.
    The state it reports is also TRUE rather than pessimistic — with the emitting workflow
    gone, the required aggregate can never be produced, so branch protection deadlocks
    every merge anyway (`mergify_config_guard --static` already reports `DIGEST-DIVERGED`
    in that state). The remedy is `tools/mergify_config_guard.py --recut`, whose whole job
    is rewriting this projection.
    """
    # ⛔ THE FIELD ITSELF AND THEN THE MATERIALISATION, because a shape rule that is not a
    # TOTAL function of the input is not a shape rule. `Probe.merge_emit_files` is an
    # annotation and `Probe` is a documented hand-buildable construction, so three classes
    # have to land on a VERDICT rather than on a traceback or a PASS:
    #   * a non-`Mapping` field  → the `.get` below would raise `AttributeError`;
    #   * a truthy NON-ITERABLE  (`17`, `1.0`, `True`) → iterating it raises `TypeError`;
    #   * a ONE-SHOT ITERATOR    (`iter`, `map`, a generator) → `all()` below EXHAUSTS it,
    #     so the later `_run_emits` comparison sees an empty sequence, matches nothing, and
    #     answers DEFERRED — a PASS on an absent aggregate whose workflow ran on the head.
    #     The empty-generator case is the worse half: it must be UNATTRIBUTABLE (no
    #     emitters), and read as "recorded, nothing matched" instead.
    # Measured at `08fab8605` for all three. Normalising ONCE, here, means every consumer
    # below sees a tuple — the materialisation is what makes the rule total.
    if not isinstance(probe.merge_emit_files, Mapping):
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the emitter projection is a "
            f"{type(probe.merge_emit_files).__name__}, not a mapping of context to workflow "
            "file names, so 'no run of the emitter' is not a measurement; re-cut with "
            "`tools/mergify_config_guard.py --recut`)"
        )
    files = probe.merge_emit_files.get(context) or ()
    if not files:
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(no emitter recorded for {context} — the committed "
            "record's `emitters` cannot say which workflow should have posted it, so its "
            "absence cannot be read as DEFERRED; re-cut with "
            "`tools/mergify_config_guard.py --recut`)"
        )
    # ⛔ A PROJECTION THAT IS NOT A SEQUENCE OF FILE NAMES CANNOT ATTRIBUTE ANYTHING, and
    # reading "no run of the emitter exists" out of it is the same conflation the
    # `head_runs` shape guard closes on the listing side. `Probe.merge_emit_files` is an
    # ANNOTATION, not an enforced type, and this module documents a hand-built `Probe` as
    # supported — so the shape is checked HERE, at the one place that turns the projection
    # into a verdict.
    #
    # Measured at `980e5131e` before this guard existed: a BARE STRING
    # (`"python-ci.yml"`, the exact JSON slip `_fixture_merge_emit_files` was added for)
    # was iterated as CHARACTERS by the basename comparison in `_run_emits`, matched
    # nothing, and answered DEFERRED — a PASS while the aggregate was absent and its
    # workflow had run on the head. That was a REGRESSION the basename normalisation
    # introduced (pre-fix, `"python-ci.yml" in "python-ci.yml"` matched, so the arm said
    # MISSING); and a non-`str` element (`None`, `17`) escaped as an `AttributeError`
    # instead of a verdict. One shape rule closes both.
    if isinstance(files, (str, Mapping)):
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the recorded emitter projection is a "
            f"{type(files).__name__}, not a sequence of workflow FILE names, so 'no run of "
            "the emitter' is not a measurement; re-cut with "
            "`tools/mergify_config_guard.py --recut`)"
        )
    try:
        files = tuple(files)
    except TypeError:
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the recorded emitter projection is a "
            f"{type(files).__name__} and cannot be enumerated, so 'no run of the emitter' "
            "is not a measurement; re-cut with "
            "`tools/mergify_config_guard.py --recut`)"
        )
    # A projection that is EMPTY once materialised is the "no emitter recorded" state, and
    # it takes the same arm a literal `()` does. Without this an empty GENERATOR read as
    # "recorded, nothing matched" → DEFERRED (PASS) while an empty TUPLE read as
    # UNATTRIBUTABLE — the same state graded two ways depending on its container.
    if not files:
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the recorded emitter projection is empty for "
            f"{context} — its absence cannot be read as DEFERRED; re-cut with "
            "`tools/mergify_config_guard.py --recut`)"
        )
    if not all(isinstance(f, str) and f for f in files):
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the recorded emitter projection carries an entry "
            "that is not a workflow FILE name, so its absence cannot be attributed; "
            "re-cut with `tools/mergify_config_guard.py --recut`)"
        )
    # ⛔ THE SPELLING IS VALIDATED AGAINST THE PROTECTED REF'S OWN WORKFLOW FILES (#11175),
    # BEFORE any run is matched — see the docstring's third paragraph. Same discipline as
    # the shape guards above: "not read" and "read, and it is empty" are different answers,
    # and neither may fall through to the DEFERRED (PASS) arm.
    if probe.base_workflow_files is None:
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the workflow files at the protected ref were not "
            "read, so a recorded emitter that matches no run cannot be told apart from a "
            "stale projection)"
        )
    if isinstance(probe.base_workflow_files, (str, bytes, Mapping)):
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the protected ref's workflow listing is a "
            f"{type(probe.base_workflow_files).__name__}, not a sequence of file names, so "
            "'the recorded emitter still exists' is not a measurement)"
        )
    try:
        present_files = tuple(probe.base_workflow_files)
    except TypeError:
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the protected ref's workflow listing is a "
            f"{type(probe.base_workflow_files).__name__} and cannot be enumerated, so "
            "'the recorded emitter still exists' is not a measurement)"
        )
    if not all(isinstance(f, str) and f for f in present_files):
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the protected ref's workflow listing carries an "
            "entry that is not a file name, so 'the recorded emitter still exists' is not "
            "a measurement)"
        )
    # Basenames on BOTH sides: the record may carry `python-ci.yml` or
    # `.github/workflows/python-ci.yml` (both spellings are in the wild, and `_run_emits`
    # reduces the same way), so the comparison must not depend on which one this record uses.
    present = {f.rsplit("/", 1)[-1] for f in present_files}
    stale = sorted({f.rsplit("/", 1)[-1] for f in files} - present)
    if stale:
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the record's `emitters` project onto "
            f"{', '.join(stale)} — no such workflow file exists at the protected ref "
            f"({len(present)} workflow file(s) known), so the projection is STALE and 'no "
            "run of the emitter on this head' is not evidence of deferral; re-cut with "
            "`tools/mergify_config_guard.py --recut`)"
        )
    if probe.head_runs is None:
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the head's workflow runs could not be read, so "
            "absence cannot be told apart from a pending aggregate)"
        )
    # The same materialisation on the LISTING side: a one-shot iterator would be exhausted
    # by the `all(...)` below, leaving the match loop with nothing to iterate and reporting
    # "no run of the emitter exists" from a listing nobody finished reading.
    if isinstance(probe.head_runs, (str, bytes, Mapping)):
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the head's run listing is a "
            f"{type(probe.head_runs).__name__}, not a sequence of run entries, so 'no run "
            "of the emitter' is not a measurement)"
        )
    try:
        head_runs = tuple(probe.head_runs)
    except TypeError:
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the head's run listing is a "
            f"{type(probe.head_runs).__name__} and cannot be enumerated, so 'no run of "
            "the emitter' is not a measurement)"
        )
    # A listing with an unreadable ENTRY is not a reading of the listing — the same rule
    # the fixture door enforces (`_fixture_head_runs`). "No run matched" is a MEASUREMENT
    # only when every entry was readable, so a hand-built `Probe` carrying a malformed
    # entry must land on the same FAIL, never on the DEFERRED (PASS) arm.
    if not all(_readable_run(run) for run in head_runs):
        return "unattributable", (
            f"{context}=UNATTRIBUTABLE(the head's run listing carries an entry that is not "
            "a READABLE run — not a mapping, or carrying no workflow `path` — so 'no run "
            "of the emitter' is not a measurement)"
        )
    matched = [_run_file(run) for run in head_runs if _run_emits(run, files)]
    if matched:
        return "missing", (
            f"{context}=MISSING(its workflow {matched[0]!r} ran on this head, so the "
            "aggregate is pending creation or never posted — absence is not deferral)"
        )
    return "", f"{context}={DEFERRED}(no {'/'.join(files)} run on this head)"


def _run_path(run: Mapping[str, Any]) -> str:
    """The workflow FILE PATH a run entry carries, or ``""`` when it carries none.

    ``path`` is the handle ``emitters`` is matched against, so an entry that does not
    carry a non-empty one is not attributable at all — see `_readable_run`.
    """
    path = run.get("path")
    return path.strip() if isinstance(path, str) else ""


def _run_file(run: Mapping[str, Any]) -> str:
    """`.github/workflows/python-ci.yml` -> `python-ci.yml` (what `emitters` records)."""
    return _run_path(run).rsplit("/", 1)[-1]


def _readable_run(run: Any) -> bool:
    """Is this entry a run attribution can READ — a mapping carrying a workflow path?

    ⛔ ``path`` IS LOAD-BEARING, exactly like ``event`` in `_run_emits`. `_run_file` maps a
    missing ``path`` to ``""``, which matches no emitter, so an entry that merely OMITS the
    field falls through to "no run of the emitter exists" — the DEFERRED (PASS) arm — on a
    listing nobody could actually read. Measured at `b45af8abd`: a hand-built
    ``head_runs=({"event": "pull_request"},)`` graded ``python-ci-gate=DEFERRED(merge-time)``
    and the gate **PASSed**, while the same entry WITH its ``path`` graded ``MISSING``
    (a FAIL). Both doors that can produce a listing must land such an entry on
    UNATTRIBUTABLE: ``_fixture_head_runs`` (a fixture) and `_merge_time_absence`' own guard
    (a hand-built ``Probe``, which this module documents as supported).
    """
    return isinstance(run, Mapping) and bool(_run_path(run))


def _run_emits(run: Mapping[str, Any], files: tuple[str, ...]) -> bool:
    """Did this run come from one of the workflows that emit the context, on a PR?

    The event filter is load-bearing in the other direction: `head_sha` also lists
    `push`/`workflow_dispatch` runs, and a `push` run of the same sha is NOT evidence
    that the PR-triggered aggregate was created. An absent `event` is not filtered out —
    the documented API always returns it, so a payload without one must not silently
    downgrade a FAIL to a DEFER.
    """
    if not isinstance(run, Mapping):
        # A malformed entry is not evidence that the workflow ran, and it must not be a
        # traceback either: `check_gates` converts every unreadable input into a VERDICT,
        # and an AttributeError escaping to the CLI exits 1 — which a caller reads as
        # "a required check is red" rather than "I could not read the runs".
        return False
    # ⛔ BOTH SIDES ARE REDUCED TO A BASENAME HERE, and this is the ONE place that does it.
    # `_run_file` already reduces the RUN side; the EMITTER side arrives either from the
    # committed record (`required_set._snapshot_emitters`, which stores the value verbatim)
    # or from the `--fixture` door, and nothing guarantees those spell the same thing. A
    # record naming `.github/workflows/python-ci.yml` used to match no run at all, leaving
    # the match set empty so `_merge_time_absence` answered DEFERRED — a PASS on a head
    # where the emitting workflow really ran. That is the record-side twin of the
    # `_readable_run` `path` guard: an emitter value that cannot match is not evidence that
    # nothing emitted it. Normalising at the comparison keeps it fail-closed for every door.
    if _run_file(run) not in {f.rsplit("/", 1)[-1] for f in files if isinstance(f, str) and f}:
        return False
    event = run.get("event")
    return not isinstance(event, str) or event in PR_TRIGGERS


def check_gates(probe: Probe) -> Check:
    """Every required ENTRY context must have a newest attempt concluded ``success``.

    ``merge_conditions`` names are a different case, and the difference is ATTRIBUTION,
    not tolerance: an absent merge-time name is ``DEFERRED(merge-time)`` (a PASS) **only**
    when nothing shows its emitting workflow ran on this head — then Mergify settles it on
    ``mergify/merge-queue/<sha>``. When a PR-triggered run of that workflow IS on the head,
    the aggregate is pending or never posted and the absence is ``MISSING`` (a FAIL); when
    the attribution or the run listing cannot be read, it is ``UNATTRIBUTABLE`` (also a
    FAIL, never a deferral). A merge-time check that ran here and is red fails too, because
    a measured red is evidence about this tree wherever it is judged. See
    ``_merge_time_absence`` for the three arms.

    In-flight is a FAIL here, not a wait: a head whose checks have not concluded is not
    ready *now*, and the answer the owner needs is about now.
    """
    # ⛔ THE REQUIRED SET'S PROVENANCE IS PART OF THE VERDICT ON EVERY ARM THAT JUDGES THE
    # SET, including the failing and the refuses-because-empty ones. The set is read from a
    # local ref that the read never fetches, so the sha in `required_raw` is the ONLY honest
    # statement about what was certified — and it used to reach output only through the two
    # unreadable arms, leaving a READY (and any named-check failure) silent about provenance.
    # Every arm below carries it, the check-runs-unreadable one included.
    source = probe.required_raw or "required-set: not read"
    if probe.required is None or probe.required_deferred is None:
        if probe.required_config_error:
            # The repo's own contract is violated (record ↔ config divergence, or a record
            # whose `live_result` is not clean). This is a CONFIGURATION fact, so it refuses
            # as `FAIL`, NOT `FAIL(unreadable)`: `unreadable` means "could not be read,
            # RETRY" here, and no retry repairs a config. Same split the every-required-
            # check-is-merge-time arm below states, and the same split `--required-set`
            # keeps (`REQUIRED-SET-CONFIG-ERROR` vs `REQUIRED-SET-UNREADABLE`).
            return Check(
                "gates",
                False,
                probe.required_raw or "required context set is empty",
                f"required-set-config-error:{probe.required_config_error}",
            )
        # An EMPTY required set is not "nothing is required" — it is a set that was not
        # read. Zero contexts, a truncated payload, or a hand-written fixture must never
        # read as an unconditional pass. (`is None` is the UNREADABLE case precisely so
        # the empty-but-read configurations below can be diagnosed as what they are.)
        return Check(
            "gates",
            False,
            probe.required_raw or "required context set is empty",
            "unreadable",
            unreadable=True,
        )
    if not probe.required and not probe.required_deferred:
        return Check(
            "gates",
            False,
            probe.required_raw or "required context set is empty",
            "unreadable",
            unreadable=True,
        )
    if not probe.required:
        # A READABLE config whose every check is merge-time: nothing is decided at the
        # head, so PASS would be a claim about nothing. This is a CONFIGURATION fact, not a
        # read failure — so it refuses as `FAIL`, NOT `FAIL(unreadable)`: `unreadable`
        # means "could not be read, RETRY" in this module's vocabulary, and retrying will
        # not change a config.
        return Check(
            "gates",
            False,
            f"entry=0 merge-time={len(probe.required_deferred)} [{source}]",
            "every required check is merge-time, so nothing is evaluated against the PR head",
        )
    if probe.groups is None:
        return Check(
            "gates",
            False,
            f"{probe.groups_raw or '-'} [grouping from ci_verdict; {source}]",
            "unreadable",
            unreadable=True,
        )
    # ⛔ The BUCKET is the check's JOB NAME, because that is what `.mergify.yml` names:
    # `check-success=python-ci-gate` IS the job name (`python-ci-gate`, `docs`,
    # `ai-review-gate`). Matching anything else would leave a required context unmatched
    # forever. What is NOT re-derived here is the GROUPING — whether two check-runs are
    # the same workflow's next attempt, and which attempt is newest, is `ci_verdict`'s
    # (`(app.slug, workflow, job)`, latest by `id`). Two workflows may therefore publish
    # one job name and land in one bucket: every group in it must then pass, which is the
    # fail-CLOSED direction (a bad attempt in either workflow fails the context) and is
    # why the spread below is over all matching groups rather than the newest only.
    by_job: dict[str, list[ci_verdict.GroupVerdict]] = {}
    for group in probe.groups:
        by_job.setdefault(group.job, []).append(group)

    failed: list[str] = []
    seen: list[str] = []
    for context in probe.required:
        groups = by_job.get(context) or []
        if not groups:
            # A required check that is ABSENT is a FAIL, never a skip — for an ENTRY
            # context. (`merge_conditions` absence is attributed, not deferred on sight —
            # see `_merge_time_absence` below.)
            failed.append(f"missing:{context}")
            seen.append(f"{context}=MISSING(no check-run on this head)")
            continue
        tokens = []
        for group in sorted(groups, key=lambda g: g.check_id or 0, reverse=True):
            token = _gate_token(group)
            seen.append(f"{context}={group.status}/{(group.conclusion or 'null')}#{group.check_id}")
            tokens.append(token)
        # ⛔ EVERY failing token is reported, never just the newest. When a check's
        # workflow identity cannot be resolved (a `pull_request_target` run is absent from
        # `actions/runs?head_sha=`, so ci_verdict falls back to a per-run key), ONE required
        # context has SEVERAL groups — and reporting only the newest would let a fresh
        # `queued` attempt HIDE a measured `failure` sitting right behind it. That is the
        # masking the voided-red rule exists to stop inside ci_verdict; reporting one token
        # here would reintroduce it in this consumer.
        for token in tokens:
            if token:
                failed.append(f"{token}:{context}")

    # ── MERGE-TIME contexts (`.mergify.yml` `merge_conditions`) ─────────────
    # ABSENCE AT THE HEAD IS NOT AUTOMATICALLY "DEFERRED". Mergify evaluates these on
    # the queue branch, but that says nothing about whether the check was CREATED here
    # yet — and an Actions job with unmet `needs` has no check-run at all, so a PENDING
    # aggregate and a workflow that never ran on this head are indistinguishable from
    # `groups` alone. Re-measured 2026-11-05: `python-ci-gate` DOES report on author
    # heads (`queued` on PR 11097, `success` on 11100, `failure` on 11096), and PR
    # 11113's head had `test (g)` red with the aggregate absent. Grading that "absent"
    # as DEFERRED is a PASS arm over evidence nobody read, and it contradicts this
    # function's own rule that an in-flight check is a FAIL, not a wait — the verdict
    # would depend on whether GitHub had created the check-run object yet.
    #
    # So absence is attributed with `_merge_time_absence`: DEFERRED only when no
    # PR-triggered run of the EMITTING WORKFLOW happened on this head; a FAIL when it
    # did (its aggregate is pending or never posted — both are non-green), and a FAIL
    # when the emission cannot be attributed at all.
    deferred = 0
    for context in probe.required_deferred or ():
        groups = by_job.get(context) or []
        if not groups:
            token, note = _merge_time_absence(probe, context)
            seen.append(note)
            if token:
                failed.append(f"{token}:{context}")
            else:
                # `_merge_time_absence` answers `("", …)` on exactly one arm: DEFERRED.
                deferred += 1
            continue
        for group in sorted(groups, key=lambda g: g.check_id or 0, reverse=True):
            seen.append(f"{context}={group.status}/{(group.conclusion or 'null')}#{group.check_id}")
            token = _gate_token(group)
            if token:
                failed.append(f"{token}:{context}")

    raw = "; ".join(seen) if seen else "no check-runs on this head"
    if failed:
        return Check(
            "gates",
            False,
            f"entry={len(probe.required)} {raw} [{source}]",
            ",".join(failed),
        )
    return Check(
        "gates",
        True,
        # `deferred=` is a count of the DEFERRALS THIS VERDICT ACTUALLY MADE, not of the
        # declared merge-time contexts: those are two different numbers whenever a
        # merge-time check also reported on this head, and printing the declared count
        # asserted a deferral of a check the same line reports as measured green. The
        # declared count is still visible — one `seen` note per context below — so nothing
        # is hidden by counting the real thing.
        f"entry={len(probe.required)} all/{REQUIRED_CONCLUSION} "
        f"deferred={deferred} {raw} [{source}]",
    )


# ── condition 3 — review, bound to the live head ────────────────────────────


def check_review(probe: Probe) -> Check:
    """A valid review record must cover what would merge, at the LIVE head.

    ``record-review.sh --check-carry`` is the ONE way to ask (it runs the same predicate
    the merge gate runs), so this tool calls it rather than re-deriving "is the record
    stale". Its exit/doc contract:

    ============ ===== ==========================================================
    token        exit  meaning
    ============ ===== ==========================================================
    head         0     the record is bound to the CURRENT head
    carry        0     the reviewed DIFF is unchanged across the head move
    fresh        1     the diff CHANGED — the record does not cover the merge
    unreadable   3     the live diff could not be read (RETRY, never an assumption)
    nodigest     5     the record carries no usable diff digest
    carry-patch  6     the patch is identical but the rendered diff moved (re-record)
    norecord     4     no review record at all
    ============ ===== ==========================================================
    """
    raw = probe.review_raw or f"token={probe.review_token or '-'} exit={probe.review_exit}"
    raw += f" record-verdict={probe.review_verdict or '-'}"
    if probe.review_token is None:
        return Check("review", False, raw, "unreadable", unreadable=True)
    if probe.review_token in REVIEW_OK_TOKENS and probe.review_exit == 0:
        # ⛔ `record-review.sh --check-carry` returns `head`/`carry` at exit 0 for a
        # `not-clean` record too — it reports that verdict on stderr and deliberately leaves
        # the exit code alone (agent-infra#3400). Keying on the token alone therefore
        # CERTIFIED a record whose own verdict is "not clean", the strongest available
        # statement that the PR is not ready. The record's `verdict` field is the
        # structured form of that statement, and it outranks the token.
        if probe.review_verdict is None:
            return Check(
                "review",
                False,
                raw,
                "record-verdict-unreadable",
                unreadable=True,
            )
        if probe.review_verdict.strip().lower() not in CLEAN_REVIEW_VERDICTS:
            return Check("review", False, raw, f"record-not-clean:{probe.review_verdict}")
        return Check("review", True, raw)
    if probe.review_token == "unreadable":
        return Check("review", False, raw, "diff-unreadable-retry", unreadable=True)
    return Check("review", False, raw, f"not-bound:{probe.review_token}")


# ── condition 4 — coverage of the declared union ────────────────────────────


def check_coverage(probe: Probe) -> Check:
    """The CI selection must cover the UNION of the touched files.

    Two ways this fails, and both are the "hand-picked subset" the brief names:

    1. **A declared union that the tree under-delivers** — a fold/batch PR that says it
       carries ``#A, #B, #C`` but whose diff is MISSING one of them. The checks then
       validated less than the PR claims, and a green gate is evidence about the wrong set.
       ⛔ Decided on the DIFFERENCE (``declared - touched``), never on the subset relation:
       this arm shipped as ``touched < declared``, a PROPER-subset test that fires only when
       the diff carries nothing outside the declaration — so ONE extra file
       (``config/ci-surfaces.yml``, ``MEMORY.md``: exactly the append-only registrars a fold
       writes) turned a wholly absent constituent into ``union=declared(3)`` + PASS, with the
       missing file never named. An EXTRA file must not be able to switch a fail-closed
       condition off (#10975 review round 22, reproduced on that tree).
    2. **A touched file for which the selection schedules NO test leg at all** —
       ``select()`` returns no surface, ``full is False`` and an EMPTY ``test_files``. An
       unfamiliar path is not this: ``select()`` fails closed to FULL for unknown paths,
       and a path the manifest maps to no surface still runs the always-on core smoke set
       (measured: ``select(["docs/ontology/NOTES.md"])`` → ``surfaces=[]``,
       ``full=False``, with the tier-1 smoke files as ``test_files``). Only a genuinely
       empty leg means nothing ran, and that is what fails here. ⛔ The example is a docs
       path on purpose: it used to be ``tools/ready_for_owner.py``, but #10975 put that
       tool in ``ci_selection.TOOL_CARVEOUTS`` so a tool-only diff fails closed to the
       FULL matrix — the example now measures ``full=True``, which is exactly the
       "a restated measurement goes stale the moment the thing it measures is fixed"
       trap this module warns about elsewhere.

    A PR with no declared union is covered by its own diff — that is the declared set,
    not a hand-picked one.

    ⛔ A DECLARED union that could not be READ is ``FAIL(unreadable)``. It must never be
    silently downgraded to "no declared union": the two states resolve to the same
    ``union is None``, and conflating them would disable this arm with one transient 502
    while the raw line still claims ``union=pr-diff``.
    """
    if probe.touched is None or not probe.touched:
        return Check("coverage", False, probe.touched_raw or "-", "unreadable", unreadable=True)
    if probe.union_unreadable:
        return Check(
            "coverage",
            False,
            probe.union_raw or "declared union unreadable",
            "unreadable",
            unreadable=True,
        )
    if probe.uncovered is None:
        return Check("coverage", False, probe.coverage_raw or "-", "unreadable", unreadable=True)
    touched = set(probe.touched)
    raw = probe.coverage_raw or f"files={len(touched)}"
    if probe.union is not None and not probe.union:
        return Check(
            "coverage",
            False,
            f"{raw} union=declared(0) {probe.union_raw}".strip(),
            "empty-declared-union",
        )
    if probe.union is not None:
        declared = set(probe.union)
        missing = sorted(declared - touched)
        if missing:
            return Check(
                "coverage",
                False,
                f"{raw} union={len(declared)} touched={len(touched)} missing={missing[:5]}",
                f"hand-picked-subset:{len(missing)}",
            )
        raw = f"{raw} union=declared({len(declared)})"
    else:
        raw = f"{raw} union=pr-diff"
    if probe.uncovered:
        return Check(
            "coverage",
            False,
            f"{raw} uncovered={list(probe.uncovered)[:5]}",
            "uncovered-files",
        )
    return Check("coverage", True, raw)


# ── condition 5 — the declared exclusions ───────────────────────────────────


def check_exclusions(probe: Probe) -> Check:
    """The four things that make a PR not-a-candidate even with green gates.

    * a **declared exclusion** (``DECLARED_EXCLUSIONS``) — quoted verbatim;
    * a **declared reversal awaiting an ``OVERRIDES:`` line** — the departure from a
      recorded decision is the owner's to rule on, not the gate's to wave through;
    * **``CONFLICTING`` with a dirty tree** — note the ORDER: the flag alone is NOT a
      failure (GitHub's cached flag is stale often enough that #10790 read DIRTY after
      its fix landed). The authoritative tree read is condition 1; the flag is only a
      diagnosis label attached when the tree agrees;
    * a **head younger than 15 minutes** — evidence has not settled.
    """
    reasons: list[str] = []
    parts: list[str] = []

    # The PR must still be OPEN and unmerged: a merged PR cannot be merged from the UI, and
    # certifying one opens a dead tab (measured: #10916, merged, printed READY before this).
    if not probe.pr_state:
        # The one place on the newest surface where an ABSENT field would otherwise
        # default to the PASSING side. A payload with no `state` is not "open".
        return Check(
            "exclusions",
            False,
            "the PR payload carried no `state` field",
            "unreadable",
            unreadable=True,
        )
    if probe.head_stable is None:
        return Check(
            "exclusions",
            False,
            "the closing head re-read was unreadable",
            "unreadable",
            unreadable=True,
        )
    if probe.head_stable is False:
        reasons.append("head-moved-mid-read")
        parts.append("the PR head moved during the read")
    if probe.merged is None:
        return Check(
            "exclusions",
            False,
            "the PR payload carried no `merged` field",
            "unreadable",
            unreadable=True,
        )
    if probe.merged or probe.pr_state.lower() != "open":
        reasons.append(f"not-open:{probe.pr_state}{'-merged' if probe.merged else ''}")
        parts.append(f"state={probe.pr_state} merged={str(probe.merged).lower()}")

    if probe.labels is None:
        return Check("exclusions", False, probe.labels_raw or "-", "unreadable", unreadable=True)

    # ⛔ THE QUEUE-ENTRY GUARD. `.mergify.yml`'s `queue_conditions` carry ONE non-check
    # condition — `-label=superseded-by-fold` — and it is the one entry this gate did not
    # model, so a constituent PR left OPEN and labelled by `tools/batch_open.py` (its
    # `label_only()` path: "gate queue entry, close nothing") was green + reviewed + old and
    # read READY-FOR-OWNER, while the queue REFUSES it at entry. The owner merges from the
    # UI, which bypasses Mergify entirely, so the constituent would land separately and the
    # fold would re-apply the same diff to a moved main — the exact outcome recorded at
    # `.mergify.yml`'s own `superseded-by-fold` note. A gate that can allow what the queue
    # refuses is the false PASS this tool exists to prevent.
    blocked_labels = sorted(set(probe.labels) & QUEUE_BLOCKING_LABELS)
    if blocked_labels:
        reasons.append("queue-blocking-label:" + ",".join(blocked_labels))
        parts.append(f"labels={sorted(probe.labels)}")

    declared = DECLARED_EXCLUSIONS.get(probe.pr)
    if declared:
        reasons.append(f"declared-exclusion:#{probe.pr}")
        parts.append(declared)

    body = probe.body or ""
    if REVERSAL_RE.search(body) and not OVERRIDES_RE.search(body):
        reasons.append("reversal-awaiting-overrides")
        parts.append("body declares a reversal but carries no OVERRIDES: line")

    flag = (probe.mergeable or "UNKNOWN").upper()
    if flag == "CONFLICTING" and probe.tree is not True:
        reasons.append("conflicting-flag-with-dirty-tree")
        parts.append(f"mergeable={probe.mergeable} and merge-tree is not clean")
    elif flag == "CONFLICTING":
        parts.append(f"mergeable={probe.mergeable} (flag IGNORED: merge-tree is clean)")

    if probe.is_draft is None:
        return Check(
            "exclusions",
            False,
            "the PR payload carried no `draft`/`isDraft` field",
            "unreadable",
            unreadable=True,
        )
    if probe.is_draft:
        reasons.append("draft")
        parts.append("isDraft=true")

    age_s: int | None = None
    committed = _parse_instant(probe.head_committed_at)
    if committed is None:
        return Check(
            "exclusions",
            False,
            probe.head_committed_at or "-",
            "unreadable",
            unreadable=True,
        )
    age = probe.now - committed
    age_s = int(age.total_seconds())
    parts.append(f"head-age={age_s}s")
    if age < HEAD_MIN_AGE:
        reasons.append(f"head-too-young:{age_s}s")
        parts.append(f"< {int(HEAD_MIN_AGE.total_seconds())}s")

    raw = f"mergeable={probe.mergeable} mergeState={probe.merge_state or '-'} " + " ".join(parts)
    if reasons:
        return Check("exclusions", False, raw.strip(), ",".join(reasons))
    return Check("exclusions", True, raw.strip())


def _parse_instant(value: str) -> datetime | None:
    """Parse a GitHub ISO-8601 instant. ``None`` when it is absent or unparseable."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


# ── condition 6 — no OPEN blocking finding ──────────────────────────────────


def _line_finding(text: str) -> bool:
    """A line-leading (or quoted) `[P1] file:line — …` finding — but NOT a severity TALLY.

    `P1: 0, P2: 0, P3: 2` inside an `**Outcome: CLEAN.**` body is a REVIEW OF A CLEAN ROUND,
    and matching it as a finding made a P3-only PR permanently uncertifiable (round 10).
    A finding names a LOCATION; a tally names a count.
    """
    for match in BLOCKING_RE.finditer(text):
        tail = text[match.end() : match.end() + 12]
        # `P1: 0` / `**P1**: none.` / `P1: nil` / `P1: zero` is a COUNT, not a finding.
        if re.match(r"\s*:?\s*(?:0|none|nil|zero)\b", tail):
            continue
        return True
    return False  # a zero count is never a finding


def _grades_as_blocking(text: str, item: Mapping[str, Any]) -> bool:
    """Does this review artifact record a blocker? Every arm is independent; any one blocks.

    HARD arms always block. SOFT arms (a declared COUNT, and a severity named anywhere) are
    suppressed by an explicit ``0 blocking`` / ``no blocking <thing>.`` — a P3-only round
    that MENTIONS the P2 it fixed must stay certifiable, or a clean PR can never open the
    owner's tab again. A blocking severity in FIELD position is hard, so a "0 blocking"
    phrase can never cancel a real finding.
    """
    issues = ISSUES_RE.search(text)
    severity = _declared_severity(text)
    severities = set(ANY_SEVERITY_TOKEN_RE.findall(text))
    # A declaration only counts in FIELD position (its own line, as in the `ISSUE:` block,
    # or beside an ISSUE-block sibling field) — prose ABOUT a past finding is not a finding.
    severity_is_field = bool(SEVERITY_LINE_START_RE.search(text)) or (
        severity is not None and bool(SIBLING_FIELD_RE.search(text))
    )
    zero_blocking = bool(ZERO_BLOCKING_RE.search(text))
    # A body that DECLARES it named nothing blocking, and names no P0/P1/P2 token anywhere,
    # may still say "issues found" — `**OUTCOME: 2 issues found (both P3), no blocking
    # defect.**` is a P3-only summary, not an open finding.
    p3_only_declared = zero_blocking and not (severities & {"0", "1", "2"})

    hard = bool(
        _line_finding(text)
        or (issues and int(issues.group(1)) > 0)
        or (
            ISSUES_FOUND_RE.search(text)
            and not p3_only_declared
            and not EXPLICIT_CLEAN_OUTCOME_RE.search(text)
        )
        or SEVERITY_LOCATED_RE.search(text)
        or TALLY_NONZERO_RE.search(text)
        or BRACKET_SEVERITY_RE.search(text)
        or _table_finding(text)
        or BLOCKING_COUNT_RE.search(text)
        or COUNT_BEFORE_TOKEN_RE.search(text)
        or ONE_OPEN_COUNT_RE.search(text)
        or ONE_OPEN_CUE_BEFORE_COUNT_RE.search(text)
        # A severity token beside an ISSUE-block SIBLING is a finding even when the body
        # never uses the word "severity" — `4 × `P2`, `check_type: bug`, `origin: diff``
        # is the dominant shape in the fold PRs (#10902/#10909/#10920).
        or (bool(SIBLING_FIELD_RE.search(text)) and bool(severities & {"0", "1", "2"}))
        or (SEVERITY_HEADING_RE.search(text) and not EXPLICIT_CLEAN_OUTCOME_RE.search(text))
        # The recorder's own unconditional arms (1) and (1b).
        or NON_CLEAN_RE.search(text)
        # Its arm (2): loose `not…clean`, suppressed by an explicit `Outcome: clean`.
        or (LOOSE_NEGATIVE_RE.search(text) and not EXPLICIT_CLEAN_OUTCOME_RE.search(text))
        # ⛔ A formal unresolved change request is a blocking record whatever its body says.
        or str(item.get("state") or "").upper() == CHANGES_REQUESTED
        or severity_is_field
    )
    if hard:
        return True
    # ⛔ `zero_blocking` is checked AFTER the multi-declaration case, not before: one round's
    # "0 blocking" must not short-circuit a P1 declared on another round (round-9 P1).
    if not (p3_only_declared or EXPLICIT_CLEAN_OUTCOME_RE.search(text)) and LABEL_NOUN_RE.search(
        text
    ):
        return True
    # A COUNTED FINDING (`4 defects remain unresolved`) is a SOFT arm: a body that declares
    # it named nothing blocking is exempt, the same rule a declared count gets.
    all_p3 = bool(severities) and severities <= {"3"}
    if not (zero_blocking or all_p3) and COUNTED_FINDING_RE.search(text):
        return True
    counts = _declared_counts(text)
    if not counts:
        return False
    if len(counts) > 1:
        # SEVERAL declarations (a per-round history): a `0 blocking` on one round must not
        # cancel a P1 declared on another. Only an explicit blocking severity on ANY round
        # blocks, so an all-P3 history stays certifiable.
        return bool(severities & {"0", "1", "2"})
    # ⛔ A P0/P1/P2 NAMED INSIDE THE COUNT DECLARATION ITSELF outranks a `no blocking`
    # phrase in the same body — `**Outcome: 2 findings (1 × P1, 1 × P2). No blocking
    # defect.**` must block (round 12). A severity merely MENTIONED elsewhere (the P2 a
    # previous round fixed) must not, or a clean round can never be certified.
    declared_text = "".join(
        match.group(0)
        for match in FINDINGS_DECLARED_RE.finditer(text)
        if match.group(2) or match.group(3)
    )
    if set(ANY_SEVERITY_TOKEN_RE.findall(declared_text)) & {"0", "1", "2"}:
        return True
    if severities & {"0", "1", "2"} and not zero_blocking:
        return True
    if zero_blocking:
        # ONE declaration that says it named nothing blocking — e.g. a P3-only round that
        # MENTIONS the P2 it just fixed. Trust the declaration.
        return False
    return any(c > 0 for c in counts) and not (severities and severities <= {"3"})


def _declared_severity(text: str) -> str | None:
    match = FINDINGS_DECLARED_RE.search(text)
    if not match:
        return None
    return (match.group(1) or "").upper() or None


def _declared_counts(text: str) -> list[int]:
    """Every count this body declares, in order (a per-round history declares several)."""
    out: list[int] = []
    for match in FINDINGS_DECLARED_RE.finditer(text):
        value = _int_or_none(match.group(2) or match.group(3))
        if value is not None:
            out.append(value)
    return out


def check_blocking_finding(probe: Probe) -> Check:
    """No P0/P1/P2 finding may still be open — the last word must be a clean verdict.

    "Open" is decided by ORDER, not by prose: walk the PR's comments and review bodies
    chronologically; a clean verdict closes whatever was open, and a blocking finding
    reopens it. Whatever the LAST one was, that is the state. Counting findings instead
    would read a fixed P0 as still open forever.
    """
    if probe.comments is None:
        return Check(
            "blocking-finding",
            False,
            probe.comments_raw or "-",
            "unreadable",
            unreadable=True,
        )
    open_finding: dict[str, Any] | None = None
    last_clear: dict[str, Any] | None = None
    blocking_seen = 0
    for item in sorted(
        probe.comments, key=lambda c: (str(c.get("created_at") or ""), int(c.get("id") or 0))
    ):
        text = str(item.get("body") or "")
        # ⛔ EVERY ARM IS EVALUATED INDEPENDENTLY AND THEY ARE OR'ED. Making the declaration
        # arm EXCLUSIVE (an `if/elif/else` chain) let ONE prose severity token mask the
        # recorder's own unconditional abort markers and the reviewer's `[P1]`/`ISSUES n`
        # verdicts — measured at the previous head: `Auto-fix stalled … see the severity: P2
        # note.`, `ISSUES 2` + `I addressed the severity: P2 comment`, and a `[P1] … still
        # open` line all read NEUTRAL. That is the same masking class as the round-7→8
        # regression, so the arms no longer compete.
        blocking = _grades_as_blocking(text, item)
        if blocking:
            blocking_seen += 1
            open_finding = item
            continue
        if asserts_a_clear(text) or str(item.get("state") or "").upper() == APPROVED:
            # A formal APPROVAL is a clear in its own right — otherwise a change request
            # at T1 could never be closed by an approval at T2, because each item is
            # graded on its own state forever, and the PR would read NOT-READY to eternity.
            last_clear = item
            open_finding = None
    raw = (
        f"comments={len(probe.comments)} blocking={blocking_seen} "
        f"last-clear={'-' if last_clear is None else last_clear.get('id')}"
    )
    if open_finding is None:
        return Check("blocking-finding", True, raw)
    snippet = " ".join(str(open_finding.get("body") or "").split())[:140]
    if not snippet:
        # A change request can carry its findings entirely inline. The raw value must still
        # say WHAT blocked, or the owner sees an empty «» and learns nothing.
        snippet = (
            f"review state {open_finding.get('state') or 'unknown'} with an empty body — "
            "the findings are inline"
        )
    return Check(
        "blocking-finding",
        False,
        f"{raw} open={open_finding.get('kind', 'comment')}:{open_finding.get('id')} "
        f"at={open_finding.get('created_at')} «{snippet}»",
        "open-blocking-finding",
    )


# ── evaluation ──────────────────────────────────────────────────────────────

EVALUATORS = {
    "tree": check_tree,
    "gates": check_gates,
    "review": check_review,
    "coverage": check_coverage,
    "exclusions": check_exclusions,
    "blocking-finding": check_blocking_finding,
}


def evaluate(probe: Probe) -> Verdict:
    """Run the six conditions IN ORDER. Every one runs — no short-circuit.

    Running all six (rather than stopping at the first failure) is deliberate: the owner
    and the lane both need the whole picture, and a tool that stops early hides the
    second defect behind the first.
    """
    checks = tuple(EVALUATORS[name](probe) for name in CONDITIONS)
    return Verdict(pr=probe.pr, head=probe.head, checks=checks)


def render(verdict: Verdict) -> str:
    lines: list[str] = []
    for index, check in enumerate(verdict.checks, start=1):
        line = (
            f"[{index}/{len(verdict.checks)}] {check.name:<17} {check.status:<17} raw: {check.raw}"
        )
        if check.reason:
            line += f"  reason: {check.reason}"
        lines.append(line.rstrip())
    failures = [c for c in verdict.checks if not c.passed]
    if failures:
        lines.append("")
        lines.append("REASONS:")
        for check in failures:
            lines.append(f"  {check.name}: {check.reason or 'failed'} — {check.raw}")
    lines.append("")
    lines.append(verdict.line())
    return "\n".join(lines)


# ── IO layer: build a Probe from the live world ─────────────────────────────


class Gh:
    """One bounded, cached ``gh`` reader for a single run.

    Caching is per-run on purpose: a checked PR costs one read per endpoint, and the
    three-PR proof run costs three, not twenty. ``403``/``429`` is surfaced as
    ``RateLimited`` so the caller can print ``GH-RATE-LIMITED(<endpoint>)`` rather than
    pretending the surface was empty — an unreadable surface is never "no checks".
    """

    def __init__(self, repo: str, timeout: int = GH_TIMEOUT) -> None:
        self.repo = repo
        self.timeout = timeout
        self._cache: dict[str, Any] = {}

    def pages(self, endpoint: str) -> list[Any]:
        """Every page of a paginated read, as raw JSON values (cached)."""
        key = "pages:" + endpoint
        if key in self._cache:
            return self._cache[key]
        url = endpoint if endpoint.startswith("http") else f"repos/{self.repo}/{endpoint}"
        try:
            proc = subprocess.run(
                ["gh", "api", "--paginate", url],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReadError(f"gh api {url}: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            message = detail[0] if detail else f"exit {proc.returncode}"
            lowered = message.lower()
            if "rate limit" in lowered or " 403" in lowered or "429" in lowered:
                raise RateLimited(url, message)
            raise ReadError(f"gh api {url}: {message}")
        try:
            pages = _decode_pages(proc.stdout)
        except (ValueError, TypeError) as exc:
            # ⛔ A body that is not JSON (a banner, a truncated response) must become a
            # READ FAILURE. It used to escape `Gh.pages`, so `main()` died with a
            # traceback and printed NO verdict line — the third output shape this module
            # promises never to produce.
            raise ReadError(f"gh api {url} returned unparseable JSON: {exc}") from exc
        if not pages:
            # An empty body is a READ FAILURE, never an empty surface.
            raise ReadError(f"gh api {url} returned an empty body")
        self._cache[key] = pages
        return pages

    def api(self, endpoint: str) -> Any:
        pages = self.pages(endpoint)
        return pages[0] if len(pages) == 1 else pages

    def api_fresh(self, endpoint: str) -> Any:
        """Read again, bypassing this run's cache — for the closing head witness."""
        self._cache.pop("pages:" + endpoint, None)
        return self.api(endpoint)

    def pr(self, number: int) -> Mapping[str, Any]:
        raw = self.api(f"pulls/{number}")
        if not isinstance(raw, Mapping):
            raise ReadError(f"pulls/{number}: unexpected payload")
        return raw


def _decode_pages(raw: str) -> list[Any]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        return [json.loads(text)]
    except json.JSONDecodeError:
        pass
    pages: list[Any] = []
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        value, index = decoder.raw_decode(text, index)
        pages.append(value)
    return pages


def _flatten(payload: Any) -> list[Any]:
    """``gh api --paginate`` emits one object per page; flatten list pages."""
    if isinstance(payload, Mapping):
        return [payload]
    out: list[Any] = []
    if isinstance(payload, list):
        for page in payload:
            if isinstance(page, list):
                out.extend(page)
            elif isinstance(page, Mapping):
                out.append(page)
    return out


def _run(cmd: Sequence[str], timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReadError(f"{' '.join(cmd)}: {exc}") from exc


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tri_state(payload: Mapping[str, Any], *keys: str) -> bool | None:
    """``True``/``False`` from the first PRESENT key, ``None`` when none of them is there.

    The point is the third value: an ABSENT boolean is not ``False``, and defaulting it to
    ``False`` puts the passing side on a field the gate exists to check.
    """
    for key in keys:
        if key in payload:
            return payload[key] is True
    return None


def _mergeable_flag(value: Any) -> str:
    """Normalise the ``mergeable`` flag to ``MERGEABLE``/``CONFLICTING``/``UNKNOWN``.

    The REST payload types it as a BOOLEAN (``true``/``false``/``null``) while GraphQL
    types it as one of those three STRINGS. Undefined coercion is how a flag check
    silently stops firing: ``str(True)`` is ``"True"``, which is never ``"CONFLICTING"``,
    so the CONFLICTING arm would simply never run on a REST read.
    """
    if isinstance(value, bool):
        return "MERGEABLE" if value else "CONFLICTING"
    text = str(value or "UNKNOWN").strip().upper()
    return text or "UNKNOWN"


def _read_head_commit_date(gh: Gh, head: str) -> str:
    raw = gh.api(f"commits/{head}")
    if isinstance(raw, Mapping):
        commit = raw.get("commit") or {}
        for key in ("committer", "author"):
            stamp = (commit.get(key) or {}).get("date")
            if stamp:
                return str(stamp)
    raise ReadError(f"commits/{head}: no committer date")


def _origin_slug(workdir: Path) -> str:
    """``owner/name`` of the workdir's ``origin`` remote, or ``""`` when undeterminable.

    Used only to DISCLOSE that the required set was derived from this checkout rather than
    from the repo being judged — never to gate on it.
    """
    try:
        proc = _run(["git", "remote", "get-url", "origin"], GIT_TIMEOUT, cwd=workdir)
    except ReadError:
        return ""
    if proc.returncode != 0:
        return ""
    # ⛔ ORDER MATTERS: strip the trailing slash BEFORE the `.git` suffix. A remote
    # spelled `https://github.com/owner/name.git/` (a trailing slash is legal) ends in
    # `/`, so `removesuffix(".git")` would be a no-op and the slug would keep `.git` —
    # printing `repo-scope=MISMATCH(… from owner/name.git's origin/main, judging
    # owner/name …)` on a checkout that is exact. A disclosure that cries wolf is a
    # disclosure readers learn to skip.
    url = proc.stdout.strip().rstrip("/").removesuffix(".git")
    # `git@github.com:owner/name` (scp-like) and `https://host/owner/name`.
    slug = url.rsplit(":", 1)[-1] if "://" not in url else "/".join(url.split("/")[-2:])
    return slug if slug.count("/") == 1 else ""


def _repo_scope_note(workdir: Path, repo: str) -> str:
    """One token saying WHICH REPO the required set was derived from.

    ⛔ `--repo` parameterises the GitHub reads but NOT the derivation: both inputs
    (``.mergify.yml`` and the committed record) come from ``origin/main`` of the checkout the
    tool runs in, so a genuinely foreign ``--repo`` is judged against the LOCAL repo's
    contract. The mismatch is not reachable as a false READY in practice — `_read_tree`
    fetches ``refs/pull/<n>/head`` from the LOCAL ``origin`` and refuses when the fetched sha
    ≠ the live head of the named repo, so the ``tree`` condition fails closed first — but a
    residual that only exists in a comment is one nobody reads, and the required set's
    provenance is supposed to be on every arm. So it is stated in the raw instead: a
    mismatch is VISIBLE in the same line that discloses the sha.
    """
    origin = _origin_slug(workdir)
    if not origin:
        return f"repo-scope=UNDETERMINED(required set read from {workdir}'s origin/main)"
    if origin.lower() == repo.lower():
        return f"repo-scope=origin({origin})"
    return (
        f"repo-scope=MISMATCH(required set read from {origin}'s origin/main, judging "
        f"{repo}; `--repo` does not scope the derivation)"
    )


def _read_required_contexts(workdir: Path, repo: str) -> tuple[RequiredSet, str]:
    """The required set, DERIVED from the repo — never read from the protection API.

    ``gh api repos/<o>/<r>/branches/main/protection`` needs repository **Administration:
    Read**. This repo's lane credential is a fine-grained PAT without it, so that read
    returns ``HTTP 403 {"message":"Resource not accessible by personal access token"}``
    — which reaches ``Gh.api`` as ``RateLimited`` and is RE-RAISED by ``_try``, aborting
    the run. Condition 2 therefore could not even be attempted on the credential the
    fleet actually uses, and lanes improvised differently (#10962).

    ``.mergify.yml`` clause 1 already states the invariant it was being read for
    (``live required contexts == queue_conditions union merge_conditions``), so the set
    is a pure function of a tracked file and needs NO credential at all. Deriving it
    also recovers the ENTRY/MERGE split that a flat protection payload cannot express —
    and without that split, ``python-ci-gate`` (a ``merge_conditions`` name) is absent at
    every PR head, which a flat set can only read as ``FAIL(missing)``: NOT-READY forever.

    ``docs/ci/required-contexts.json`` remains the committed witness of the LIVE
    protection read (clause 2), and ``tools.required_set`` refuses when the two DISAGREE in
    either direction — a record naming a context the config no longer derives is the #3016
    regression, and a config deriving one the record does not confirm is the same EQUALITY
    violated (the CI guard's `_clause_iv` treats both as `EXIT_DIVERGED`). The API is
    deliberately not called here: it IS the credential dependency this removes, and its
    recorded answer is already in the tree.

    ⛔ A GATE THAT READS ITS OWN SUBJECT IS DEFEATABLE BY ITS SUBJECT, so the bytes come
    from the PROTECTED ref (``origin/main``), never from the workdir being judged. Both
    inputs are tracked files: a PR that deletes a ``check-success=`` line AND the matching
    ``required_contexts`` entry in the same diff would otherwise have the snapshot certify
    the set it just shrank. See ``tools.required_set.derive_from_ref``.

    ⛔ A set that cannot be SPLIT is not evaluated: guessing which names are merge-time
    re-creates the false NOT-READY, so it is a refusal rather than a verdict.
    """
    try:
        required = derive_from_ref(workdir, f"origin/{PROTECTED_BRANCH}", timeout=GIT_TIMEOUT)
    except RequiredSetConfigError as exc:
        # A config error is a VERDICT about the repo's contract, an unreadable config is
        # "I could not read it". Both REFUSE; neither may fall back to a guessed set.
        # ⛔ They are carried as DIFFERENT classes across this boundary, because the
        # caller has to LABEL them differently: `FAIL(unreadable)` tells a lane to retry
        # the read, and no retry repairs a config/record divergence.
        raise RequiredConfigReadError(str(exc)) from None
    except (GuardUnreadable, RequiredSetUnreadable) as exc:
        # A config error is a VERDICT about the repo's contract, and an unreadable
        # config is "I could not read it". Both REFUSE; neither may fall back to a
        # guessed set.
        raise ReadError(str(exc)) from None
    raw = (
        # `required.source` already ends with `at <ref> (<sha12>)` — derive_from_ref
        # discloses what it READ, so the ref must not be appended a second time here
        # (`… at origin/main (abc)@origin/main` was the shape that bug produced).
        f"source={required.source} "
        f"entry={len(required.queue_names)} "
        f"merge-time={len(required.merge_names)} mode={required.mode} "
        f"snapshot_agrees={required.snapshot_agrees}"
    )
    raw = f"{raw} {_repo_scope_note(workdir, repo)}"
    return required, raw


def _read_check_groups(
    gh: Gh, head: str, repo: str
) -> tuple[tuple[ci_verdict.GroupVerdict, ...], tuple[Mapping[str, Any], ...], str]:
    """Newest attempt per ``(app, workflow, job)`` — delegated to ``ci_verdict``.

    The FETCH goes through this module's bounded, cached ``Gh`` reader; the GROUPING and
    the completeness check (``collect_items`` refuses a truncated listing) stay in
    ``ci_verdict``, which is the repo's single authoritative check-run reading (#5042).
    Nothing about what counts as a group or an attempt is re-derived here.

    The RUNS are returned as well as the groups, from the SAME read: they are what lets a
    consumer tell a merge-time context's absence-with-a-pending-aggregate from
    absence-because-the-workflow-never-ran, and a second, independent run read could
    disagree with the check-run payload it is paired with.
    """
    check_url = ci_verdict.CHECK_RUNS_URL.format(repo=repo, sha=head)
    run_url = ci_verdict.RUNS_URL.format(repo=repo, sha=head)
    prefix = f"repos/{repo}/"
    check_runs = ci_verdict.collect_items(
        gh.pages(check_url.removeprefix(prefix)),
        ci_verdict.CHECK_RUNS_KEY,
        check_url,
    )
    runs = ci_verdict.collect_items(
        gh.pages(run_url.removeprefix(prefix)), ci_verdict.RUNS_KEY, run_url
    )
    verdict = ci_verdict.compute_verdict(head, check_runs, runs, repo=repo)
    counts = verdict.counts
    raw = (
        f"surface={len(verdict.groups)} groups "
        f"(green={counts.get(ci_verdict.GROUP_GREEN, 0)} "
        f"red={counts.get(ci_verdict.GROUP_RED, 0)} "
        f"in-flight={counts.get(ci_verdict.GROUP_IN_FLIGHT, 0)} "
        f"absent={counts.get(ci_verdict.GROUP_ABSENT, 0)}) commit-verdict={verdict.verdict}"
    )
    return verdict.groups, tuple(runs), raw


def _read_tree(gh: Gh, pr: int, head: str, workdir: Path) -> tuple[bool | None, str]:
    """``git merge-tree --write-tree origin/main <head>`` — the authoritative tree read.

    The PR head is fetched into a LOCAL throwaway remote-tracking ref
    (``refs/remotes/ready-gate/pr-<n>``) — a read of the remote, no push, no branch of
    the PR touched. If the fetched sha is not the live head the read is REFUSED rather
    than compared: comparing a stale local object would certify a tree nobody asked about.
    """
    ref = f"refs/remotes/ready-gate/pr-{pr}"
    fetch = _run(
        ["git", "fetch", "--no-tags", "origin", f"+refs/pull/{pr}/head:{ref}"],
        GIT_TIMEOUT,
        cwd=workdir,
    )
    if fetch.returncode != 0:
        return None, f"git fetch refs/pull/{pr}/head failed: {_first_line(fetch.stderr)}"
    rev = _run(["git", "rev-parse", ref], GIT_TIMEOUT, cwd=workdir)
    fetched = (rev.stdout or "").strip()
    if rev.returncode != 0 or len(fetched) != 40:
        return None, f"git rev-parse {ref} was unreadable"
    if fetched != head:
        return None, f"fetched {fetched[:12]} != live head {head[:12]} (head moved mid-read)"
    base = _run(["git", "fetch", "--no-tags", "origin", PROTECTED_BRANCH], GIT_TIMEOUT, cwd=workdir)
    if base.returncode != 0:
        return None, f"git fetch origin {PROTECTED_BRANCH} failed: {_first_line(base.stderr)}"
    merge_tree = _run(
        ["git", "merge-tree", "--write-tree", f"origin/{PROTECTED_BRANCH}", fetched],
        GIT_TIMEOUT,
        cwd=workdir,
    )
    detail = _first_line(merge_tree.stdout) or _first_line(merge_tree.stderr)
    if merge_tree.returncode == 0:
        tree = (merge_tree.stdout or "").strip().splitlines()
        oid = tree[0] if tree else "-"
        return True, f"merge-tree write-tree ok tree={oid[:12]} base=origin/{PROTECTED_BRANCH}"
    if merge_tree.returncode == 1:
        return False, f"merge-tree CONFLICTS base=origin/{PROTECTED_BRANCH}: {detail}"
    return None, f"git merge-tree exited {merge_tree.returncode}: {detail}"


def _first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _record_verdict(pr: int, repo: str) -> str | None:
    """The RECORD's own ``verdict`` field, or ``None`` when it cannot be read.

    Follows the recorder's own file convention and its collision rule: the repo-qualified
    name first, then the legacy ``<pr>.json`` only when that record names no repo or THIS
    repo (the #426 rule the local gate and the rail both apply).
    """
    reviews = Path(os.environ.get("HOME") or "~").expanduser() / ".pi" / "agent" / "reviews"
    candidates: list[Path] = []
    if "/" in repo:
        owner, name = repo.split("/", 1)
        candidates.append(reviews / f"{owner}-{name}-{pr}.json")
    candidates.append(reviews / f"{pr}.json")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, Mapping):
            return None
        embedded = str(data.get("repo") or "")
        if embedded and embedded != repo:
            continue
        verdict = data.get("verdict")
        if isinstance(verdict, str) and verdict.strip():
            return verdict.strip()
    return None


def _review_script(workdir: Path) -> Path | None:
    """The recorder, or ``None`` when the machine-local ``scripts/`` pointer is absent.

    Its own function so a test can simulate a CI checkout (where `scripts/` is documented
    ABSENT — the pointer is gitignored and materialised per machine) without depending on
    the checkout it happens to run in. A test that needs the real file turns red for an
    ENVIRONMENT reason, and the tempting fix is then to weaken the test.
    """
    script = workdir / "scripts" / "record-review.sh"
    return script if script.is_file() else None


def _read_review(
    pr: int, repo: str, workdir: Path
) -> tuple[str | None, int | None, str, str | None]:
    """Ask the gate's OWN predicate: ``record-review.sh --check-carry``.

    The script is the single reader of "does this record cover what would merge", so it
    is invoked rather than re-implemented. Its first stdout field is the verdict token.

    ⛔ THE RECORD FILE IS PER-MACHINE (``$HOME/.pi/agent/reviews``), so a review minted on
    ANOTHER box is invisible here and reads ``norecord`` — which FAILS. That is a FALSE
    BLOCK, the acceptable direction, and it is documented as a limitation.

    ⛔ AND THE CALLER-SUPPLIED PAIR IS DELIBERATELY *NOT* PASSED THROUGH. Round 10 measured
    that forwarding ``record-review.sh --recorded-diff/--recorded-head`` turns this arm into
    a self-attestation: the recorder's supplied-pair arm short-circuits its record lookup,
    the diff digest is computable by anyone from the public diff
    (``record-review.sh --print-diff-hash <PR>``), and ``--recorded-diff aaa…a
    --recorded-head <live head>`` made condition 3 PASS on a PR with no review at all. The
    gate cannot distinguish a caller asserting a review from a caller forwarding one it
    holds, so it asks the box — and FAILS when the box has nothing. That is the only
    version of this arm that cannot be talked into a PASS.
    """
    script = _review_script(workdir)
    if script is None:
        return (
            None,
            None,
            f"{workdir / 'scripts' / 'record-review.sh'} is absent "
            "(the scripts/ pointer is not materialised)",
            # ⛔ THE FOURTH ELEMENT IS NOT OPTIONAL. This branch returned a 3-tuple while the
            # signature, both other returns and `collect_probe`'s 4-value unpack all expect a
            # verdict slot, so on any box without the `scripts/` pointer the unpack raised
            # `ValueError: not enough values to unpack (expected 4, got 3)` — the intended
            # "unreadable, name the missing pointer" refusal became unreachable and the CLI
            # fell back to `READY-GATE-ERROR` with EVERY condition failed. The verdict is
            # genuinely unknown on this path, so it is `None`.
            None,
        )
    proc = _run(["bash", str(script), "--check-carry", str(pr), repo], GIT_TIMEOUT, cwd=workdir)
    verdict = _record_verdict(pr, repo)
    if verdict is None and "NOT CLEAN" in (proc.stderr or "").upper():
        verdict = "not-clean"
    token = ""
    for line in (proc.stdout or "").splitlines():
        fields = line.split("\t")
        if fields and fields[0].strip():
            token = fields[0].strip()
            break
    digest = f"record={_field(proc.stdout, 1)} live={_field(proc.stdout, 2)}"
    message = _first_line(proc.stderr)
    raw = f"token={token or '-'} exit={proc.returncode} {digest}"
    if message:
        raw += f" «{message[:160]}»"
    if not token:
        # ⛔ THE EXIT CODES ARE THE SCRIPT'S, NOT A GUESS: `norecord` is 4 (#2234) and
        # `fresh` is 1. An earlier version mapped rc==1 to `norecord`, which told a lane
        # to RE-RECORD a diff that had changed and told it to RETRY a PR with no record
        # at all — both still FAIL, so no false READY, but both sent the lane the wrong way.
        return (
            {1: "fresh", 4: "norecord"}.get(proc.returncode, "unreadable"),
            proc.returncode,
            raw,
            verdict,
        )
    return token, proc.returncode, raw, verdict


def _field(stdout: str, index: int) -> str:
    for line in (stdout or "").splitlines():
        fields = line.split("\t")
        if len(fields) > index:
            return fields[index].strip()
    return "-"


def _read_touched(gh: Gh, pr: int) -> tuple[tuple[str, ...], str]:
    files = _flatten(gh.api(f"pulls/{pr}/files?per_page=100"))
    names: list[str] = []
    for entry in files:
        if isinstance(entry, Mapping) and entry.get("filename"):
            names.append(str(entry["filename"]))
    if not names:
        raise ReadError(f"pulls/{pr}/files returned no filenames")
    # ⛔ COMPLETENESS IS WITNESSED, not assumed: the PR payload states `changed_files`, so a
    # truncated listing is caught instead of silently shrinking the coverage read while the
    # raw line still says `files=N`. (The same rule `ci_verdict.collect_items` applies to
    # the check-run surface.)
    _pull = gh.api(f"pulls/{pr}")
    expected = _int_or_none(_pull.get("changed_files")) if isinstance(_pull, Mapping) else None
    if expected is None:
        # ⛔ An ABSENT `changed_files` is not "nothing to compare": the truncation witness
        # cannot run, so the listing is UNWITNESSED. Same doctrine as
        # `draft`/`merged`/`labels`/`state` — an absent field is never the passing side.
        raise ReadError(
            f"pulls/{pr}: the payload carries no `changed_files`, so the file listing's "
            "completeness cannot be witnessed"
        )
    if len(names) < expected:
        raise ReadError(
            f"pulls/{pr}/files: read {len(names)} of {expected} changed files — "
            "the listing is TRUNCATED"
        )
    return tuple(names), f"files={len(names)}/{expected}"


def declared_union(body: str) -> tuple[tuple[str, ...] | None, str]:
    """The union a PR DECLARES it carries, from its ``READY-GATE UNION:`` line."""
    match = UNION_RE.search(body or "")
    if not match:
        return None, "no declared union"
    entries = [p.strip() for p in re.split(r"[,\s]+", match.group(1)) if p.strip()]
    entries = [entry.removeprefix("#") for entry in entries]
    return tuple(entries), f"declared union entries={len(entries)}"


def _resolve_union(entries: tuple[str, ...], gh: Gh) -> tuple[tuple[str, ...] | None, str]:
    """Expand a declared union: ``#N`` entries become that PR's changed files.

    Returns ``(None, why)`` ONLY when the read failed. An EMPTY declaration returns
    ``((), why)`` — a malformed marker, which ``check_coverage`` fails rather than
    silently treating as "no union declared" (the same collapse the read-error arm avoids).
    """
    if not entries:
        return (), "declared union has no entries (malformed READY-GATE UNION: line)"
    resolved: list[str] = []
    prs: list[int] = []
    for entry in entries:
        ref = PR_REF_RE.match(entry)
        if ref:
            prs.append(int(ref.group(1)))
        else:
            resolved.append(entry)
    for number in prs:
        files, _ = _read_touched(gh, number)
        resolved.extend(files)
    return tuple(sorted(dict.fromkeys(resolved))), (
        f"declared union: {len(prs)} PR(s) + {len(entries) - len(prs)} path(s) "
        f"= {len(set(resolved))} file(s)"
    )


def _read_coverage(touched: tuple[str, ...]) -> tuple[tuple[str, ...] | None, str]:
    """Ask the repo's own selection function whether it covers the touched set.

    ``tools/ci_selection.select()`` is the single declared function (#1021): it either
    matches a file to a surface, falls closed to FULL, or maps it to the always-on core
    smoke set. A path therefore has NO test leg only when ``surfaces == []``,
    ``full is False`` AND ``test_files`` is empty — and that is the only shape reported as
    uncovered. (An earlier version treated ``surfaces == [] and not full`` as uncovered;
    measured, ``select(["docs/ontology/NOTES.md"])`` returns exactly that shape with
    **72 tests** scheduled, so it would have failed this tool's own PR and every PR that
    adds a docs-only path. The docstring said "recorded, not failed"; the code was
    wrong, not the docstring. ⛔ The example is deliberately a DOCS path: it used to be
    ``tools/ready_for_owner.py``, but #10975 gave that tool a
    ``ci_selection.TOOL_CARVEOUTS`` entry so a tool-only diff fails closed to the FULL
    matrix — it now measures ``full: True``, and a restated measurement goes stale the
    moment the thing it measures is fixed.)

    Returns ``(uncovered, raw)``; ``uncovered is None`` means the selection was UNREADABLE
    and the caller must FAIL rather than assume coverage.
    """
    try:
        from tools import ci_selection
    except Exception as exc:  # pragma: no cover - import guard
        return None, f"ci_selection import failed: {exc}"
    try:
        manifest = ci_selection.load_manifest()
        explain: dict[str, Any] = {}
        selection = ci_selection.select(list(touched), "pull_request", manifest, explain)
    except Exception as exc:
        return None, f"ci_selection.select failed: {exc}"
    surfaces = selection.get("surfaces") or []
    full = bool(selection.get("full"))
    uncovered: list[str] = []
    tier1_only: list[str] = []
    if not full:
        for path in touched:
            # ⛔ INSIDE the fail-closed conversion. A raise here used to escape
            # `collect_probe` entirely, so `main()` died with a traceback and printed NO
            # verdict line — a third output shape beside READY-FOR-OWNER / NOT-READY.
            try:
                one = ci_selection.select([path], "pull_request", manifest, {})
            except Exception as exc:
                return None, f"ci_selection.select([{path}]) failed: {exc}"
            if one.get("full") or (one.get("surfaces") or []):
                continue
            test_files = one.get("test_files")
            has_leg = test_files == "ALL" or bool(test_files)
            if has_leg:
                # No surface of its own, but the always-on core smoke set runs.
                tier1_only.append(path)
            else:
                uncovered.append(path)
    raw = (
        f"files={len(touched)} full={str(full).lower()} surfaces={list(surfaces)} "
        f"core-smoke-only={len(tier1_only)} no-leg={len(uncovered)}"
    )
    return tuple(uncovered), raw


def _read_comments(gh: Gh, pr: int) -> tuple[tuple[Mapping[str, Any], ...], str]:
    """Every artifact that can carry a review verdict — issue comments, review BODIES
    (with their ``state``), and INLINE review comments.

    ⛔ Each of the three was a real hole. A review can carry an EMPTY body with all its
    findings inline and its state set to ``CHANGES_REQUESTED``; reading only non-empty
    bodies dropped it entirely, so a PR with a formal unresolved change request read as
    CLEAN here.
    """
    comments = _flatten(gh.api(f"issues/{pr}/comments?per_page=100"))
    reviews = _flatten(gh.api(f"pulls/{pr}/reviews?per_page=100"))
    inline = _flatten(gh.api(f"pulls/{pr}/comments?per_page=100"))
    # A dismissed review's INLINE comments are as dismissed as its body. Keeping them made
    # a dismissed review's `[P1]` block forever — asymmetric with dropping the body.
    dismissed_review_ids = {
        item.get("id")
        for item in reviews
        if isinstance(item, Mapping) and str(item.get("state") or "").upper() == DISMISSED
    }
    out: list[Mapping[str, Any]] = []
    for kind, items in (("comment", comments), ("review", reviews), ("inline", inline)):
        for item in items:
            if not isinstance(item, Mapping):
                continue
            body = str(item.get("body") or "")
            state = str(item.get("state") or "").upper()
            # A DISMISSED review is neutral REGARDLESS of its body: it was dismissed, so
            # its findings no longer stand. Keeping a non-empty dismissed body graded its
            # `[P0]/[P1]/[P2]` lines as open forever.
            if state == DISMISSED:
                continue
            if kind == "inline" and item.get("pull_request_review_id") in dismissed_review_ids:
                continue
            # A BARE GitHub-UI review (empty body) still carries a STATE, and that state
            # is the verdict: an empty-bodied APPROVED is the commonest approval shape, and
            # dropping it made the approval arm unreachable for exactly that shape.
            if not body and state not in (CHANGES_REQUESTED, APPROVED):
                continue
            out.append(
                {
                    "id": item.get("id"),
                    "created_at": item.get("created_at") or item.get("submitted_at") or "",
                    "kind": kind,
                    "state": state,
                    # A change request with an empty body still has to say WHAT it is.
                    "body": body or f"review state {state or 'unknown'}",
                }
            )
    if not out:
        # A 200 with `[]` on all three endpoints is a legitimate "nothing was posted yet",
        # not a read failure — the distinction is `()` (read, empty) vs `None`
        # (unreadable), and conflating them made every brand-new PR FAIL as unreadable.
        return (), "comments=0"
    return tuple(out), f"comments={len(out)}"


def collect_probe(
    pr: int,
    repo: str = DEFAULT_REPO,
    workdir: Path | None = None,
    now: datetime | None = None,
) -> Probe:
    """Read the live world into a Probe. Every unreadable input is recorded, not raised.

    A failed read is ``FAIL(unreadable)`` on its own condition — never a crash (a crashed
    gate is indistinguishable from an unrun one) and never a default.
    """
    workdir = workdir or REPO_ROOT
    gh = Gh(repo)
    probe = Probe(pr=pr, repo=repo, now=now or datetime.now(UTC))

    try:
        pull = gh.pr(pr)
    except ReadError as exc:
        probe.tree_raw = probe.required_raw = probe.groups_raw = probe.touched_raw = str(exc)
        probe.review_raw = probe.comments_raw = probe.coverage_raw = str(exc)
        return probe

    # `or {}`: a present-but-NULL `head`/`base` would raise AttributeError and escape.
    head = str((pull.get("head") or {}).get("sha") or "")
    probe.head = head
    probe.base = str((pull.get("base") or {}).get("ref") or "")
    # An OMITTED `draft` is not "not a draft": read the key, do not assume it. Both
    # spellings are read — REST and the GitHub UI payload say `draft`, GraphQL says
    # `isDraft` — and `None` means "not read", which `check_exclusions` turns into
    # FAIL(unreadable). A GraphQL-shaped payload read only for `draft` would silently
    # UN-draft a draft PR.
    probe.is_draft = _tri_state(pull, "draft", "isDraft")
    probe.mergeable = _mergeable_flag(pull.get("mergeable"))
    # REST spells it `mergeable_state`; GraphQL (`gh pr view --json`) spells it
    # `mergeStateStatus`. Read both, so the reported value is the same either way.
    probe.merge_state = str(pull.get("mergeable_state") or pull.get("mergeStateStatus") or "")
    probe.body = str(pull.get("body") or "")
    probe.pr_state = str(pull.get("state") or "")
    probe.merged = _tri_state(pull, "merged")
    if probe.merged is None and pull.get("merged_at"):
        probe.merged = True
    labels = pull.get("labels")
    if isinstance(labels, list):
        probe.labels = tuple(
            str(entry.get("name"))
            for entry in labels
            if isinstance(entry, Mapping) and entry.get("name")
        )
        probe.labels_raw = f"labels={sorted(probe.labels)}"
    else:
        # An ABSENT `labels` key is not "no labels": the entry guard cannot be evaluated,
        # so it is unreadable rather than silently empty.
        probe.labels = None
        probe.labels_raw = "pulls/N carried no labels array"

    try:
        probe.head_committed_at = _read_head_commit_date(gh, head)
    except ReadError:
        # An unreadable head date makes `exclusions` FAIL(unreadable) on its own; it is
        # NOT evidence about the tree, so it must not be written into `tree_raw`.
        probe.head_committed_at = ""

    # ⛔ ORDER MATTERS: the required set is read from `origin/main`, and `_try_tree` is
    # what FETCHES it (`git fetch --no-tags origin main`). Reading the derived set first
    # would use whatever `origin/main` the worktree happened to hold — a stale protected
    # ref is exactly the "stale list is a silent deadlock" `.mergify.yml` clause 2 warns
    # about.
    #
    # ⛔ WHAT THIS ORDER DOES *NOT* BUY, stated so nobody re-derives it as a guarantee:
    # `derive_from_ref` does not fetch, and `git show` succeeds on any ref that EXISTS,
    # stale or not — so ordering alone cannot prove freshness. What holds is narrower:
    # a run that reaches **READY** has `tree=True`, which requires that same run's base
    # fetch to have succeeded. (Two `_read_tree` failure paths — a failed base fetch and a
    # failed `merge-tree` — do run after it, so "every failing path fails before the fetch"
    # is NOT true; they simply cannot reach READY.) Anything weaker than that is disclosed
    # rather than assumed: the derived `source` carries the sha it actually read, and
    # `check_gates` puts that source in its raw on every arm.
    probe.tree, probe.tree_raw = _try_tree(gh, pr, head, workdir)
    try:
        required, probe.required_raw = _read_required_contexts(workdir, repo)
    except RequiredConfigReadError as exc:
        # A CONFIGURATION verdict, not a read failure: the set is still unread (so the
        # gate still REFUSES), but the class is carried through so `check_gates` can label
        # it `FAIL` instead of `FAIL(unreadable)`. Must be caught BEFORE `ReadError`, of
        # which it is a subclass.
        probe.required = None
        probe.required_deferred = None
        probe.required_config_error = str(exc)
        probe.required_raw = f"required-set: {exc}"
    except ReadError as exc:
        # `_read_required_contexts` touches no `Gh`: it shells `git` and converts every
        # failure to `ReadError` (a `RateLimited` is a `ReadError` too, so it would land
        # here). A read that could not be made is NOT evidence about the gates, so the set
        # is left unread and `check_gates` says so.
        probe.required = None
        probe.required_deferred = None
        probe.required_raw = f"required-set: {exc}"
    else:
        probe.required = required.queue_names
        probe.required_deferred = required.merge_names
        # Only the MERGE-TIME names need attribution: an entry context's absence is
        # already a FAIL(missing), so its emitter file would never be consulted.
        probe.merge_emit_files = {
            name: required.emit_workflows(name) for name in required.merge_names
        }
        # ⛔ THE PROJECTION IS ONLY READABLE AGAINST THE FILES THAT EXIST (#11175). Carried
        # from the SAME derivation, so the spelling and the files it is validated against
        # come from one read of one commit.
        probe.base_workflow_files = required.workflow_files
    try:
        probe.groups, probe.head_runs, probe.groups_raw = _read_check_groups(gh, head, repo)
    except RateLimited:
        raise
    except (ReadError, ci_verdict.SurfaceReadError) as exc:
        # One failed read means BOTH are unknown: the groups and the run listing come from
        # the same call, and an absence graded against a half-read surface is how a
        # pending aggregate would read as a deferral.
        probe.groups, probe.head_runs = None, None
        probe.groups_raw = f"check-runs: {exc}"
    (
        probe.review_token,
        probe.review_exit,
        probe.review_raw,
        probe.review_verdict,
    ) = _try_review(pr, repo, workdir)
    probe.touched, probe.touched_raw = _try(gh, lambda g: _read_touched(g, pr))
    if probe.body:
        entries, _union_raw = declared_union(probe.body)
        # ⛔ `is not None`, NOT truthiness: an empty/malformed `READY-GATE UNION:` line
        # parses to `()`, and `if entries:` sent it down the "no union declared" branch —
        # leaving the `empty-declared-union` fail arm unreachable from the live path
        # (fixture-only), and silently certifying a fold PR with a broken marker.
        if entries is not None:
            # A DECLARED union that cannot be resolved is UNREADABLE, never "no union":
            # both write `union is None`, and conflating them would disable the
            # hand-picked-subset arm behind one transient 502.
            resolved, probe.union_raw = _try(gh, lambda g: _resolve_union(entries, g))
            probe.union = resolved
            probe.union_unreadable = resolved is None
        else:
            probe.union_raw = _union_raw
    else:
        probe.union_raw = "no PR body"
    if probe.touched:
        probe.uncovered, probe.coverage_raw = _read_coverage(probe.touched)
    else:
        probe.coverage_raw = probe.touched_raw
    probe.comments, probe.comments_raw = _try(gh, lambda g: _read_comments(g, pr))
    # ⛔ THE HEAD IS RE-WITNESSED LAST. Conditions 1-2 measured `probe.head`, while condition
    # 3's recorder evaluates the LIVE head — so a push landing in between would let
    # `READY-FOR-OWNER(<old head>)` describe a merge target whose gates were never measured.
    probe.head_stable = _witness_head(gh, pr, head)
    return probe


def _witness_head(gh: Gh, pr: int, head: str) -> bool | None:
    """Is the PR's head STILL ``head`` after everything else was read?"""
    try:
        fresh = gh.api_fresh(f"pulls/{pr}")
    except RateLimited:
        raise
    except ReadError:
        return None
    if not isinstance(fresh, Mapping):
        return None
    live = str((fresh.get("head") or {}).get("sha") or "")
    if len(live) != 40:
        return None
    return live == head


def _try(gh: Gh, fn, surface: str = "") -> tuple[Any, str]:
    try:
        return fn(gh)
    except RateLimited:
        raise
    except ReadError as exc:
        return None, f"{surface + ': ' if surface else ''}{exc}"
    except ci_verdict.SurfaceReadError as exc:
        return None, f"{surface + ': ' if surface else ''}{exc}"


def _try_tree(gh: Gh, pr: int, head: str, workdir: Path) -> tuple[bool | None, str]:
    try:
        return _read_tree(gh, pr, head, workdir)
    except ReadError as exc:
        return None, str(exc)


def _try_review(
    pr: int, repo: str, workdir: Path
) -> tuple[str | None, int | None, str, str | None]:
    try:
        return _read_review(pr, repo, workdir)
    except ReadError as exc:
        return None, None, str(exc), None


# ── CLI ─────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ready_for_owner.py",
        description=(
            "Answer READY / NOT-READY for the owner to merge a PR from the GitHub UI, "
            "with the raw value every condition read. Exit 0 only when READY."
        ),
    )
    parser.add_argument(
        "pr",
        type=int,
        nargs="?",
        default=None,
        help="pull request number (omit only with --fixture or --required-set)",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"owner/name (default {DEFAULT_REPO})")
    parser.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    parser.add_argument(
        "--workdir",
        default=str(REPO_ROOT),
        help="repo root the git/merge-tree and record-review reads run in",
    )
    parser.add_argument(
        "--fixture",
        default=None,
        help="evaluate a Probe JSON fixture instead of reading the live world (tests)",
    )
    parser.add_argument(
        "--required-set",
        action="store_true",
        help=(
            "print the REQUIRED status-check set as `.mergify.yml` declares it "
            "(queue=ENTRY, merge=merge-time) and exit; no PR number and no credential"
        ),
    )
    parser.add_argument(
        "--ref",
        default="origin/main",
        help=(
            "ref the required set is DERIVED from for --required-set (default "
            "origin/main, the ref that governs the merge). ⛔ `HEAD` reads the last "
            "COMMIT on this branch — `git show`, not the working tree — so an "
            "UNCOMMITTED edit to `.mergify.yml` is not seen; commit it, or rely on the "
            "default protected-ref read"
        ),
    )
    return parser


def report_required_set(workdir: Path, ref: str | None = "origin/main") -> int:
    """Print the derived required set. Exit 0 PASS · 2 unusable (with the REASON labelled).

    A REPORTING verb, and it is deliberately a separate output shape from the
    READY/NOT-READY contract: it answers "what is required here?", not "is this PR
    ready?". Exit ``2`` on an unusable set so "I could not read it" is never mistaken
    for "nothing is required", which is the fail-open this whole module refuses.

    ⛔ A CONFIG ERROR IS LABELLED AS ONE. Both are exit 2, but a reader comes to this
    verb to find out WHY, and `REQUIRED-SET-UNREADABLE(<the config is wrong>)` points at
    the wrong subsystem — the body would say the contract is violated while the label
    says I/O failed.

    ⛔ AN EMPTY `--ref` IS REFUSED, and the guard buys a DIAGNOSIS rather than a bypass
    guard: the fallback to the workdir was already closed by branching on `ref is not
    None`, so an empty ref would have failed closed through `git rev-parse --verify ''`
    (`fatal: Needed a single revision`) — an opaque message from the wrong subsystem, on a
    verb a human runs to learn WHY. The refusal names the problem and the reason instead.
    The workdir read stays available only as `ref=None`, an in-process diagnostic (tests),
    which no CLI flag can reach.
    """
    if ref is not None and not ref.strip():
        print(
            "REQUIRED-SET-UNREADABLE(--ref must name a ref; an empty value is not "
            "'read this worktree', because a branch must never certify its own "
            "required set)"
        )
        return 2
    try:
        required = derive_from_ref(workdir, ref) if ref is not None else derive_from_config(workdir)
    except RequiredSetConfigError as exc:
        print(f"REQUIRED-SET-CONFIG-ERROR({exc})")
        return 2
    except (GuardUnreadable, RequiredSetUnreadable) as exc:
        print(f"REQUIRED-SET-UNREADABLE({exc})")
        return 2
    except Exception as exc:
        # Same backstop discipline as `main()`: a reporting verb must always emit a
        # labelled line and a documented exit code. Every reader is meant to convert its
        # own failure, but a case nobody foresaw (a blob that will not decode, a vendor
        # surprise) must not become a traceback with an exit code that reads as a verdict.
        print(f"REQUIRED-SET-UNREADABLE({type(exc).__name__}: {exc})")
        return 2
    # `source` already carries the ref AND the sha it resolved to (derive_from_ref
    # discloses what it actually read), so do not append the ref a second time.
    print(f"source: {required.source}")
    print(f"mode: {required.mode}")
    print(f"queue_conditions (ENTRY, at the PR head): {', '.join(required.queue_names)}")
    print(f"merge_conditions (MERGE-time, deferred here): {', '.join(required.merge_names)}")
    print(f"OVERLAP: {', '.join(required.overlap) if required.overlap else '(none)'}")
    print(f"union ({len(required.names)}): {', '.join(required.names)}")
    print(f"snapshot_agrees: {required.snapshot_agrees}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.required_set:
        return report_required_set(Path(args.workdir).resolve(), args.ref)
    if args.fixture is None and args.pr is None:
        parser.error("a pull request number is required unless --fixture or --required-set is used")
    workdir = Path(args.workdir).resolve()
    try:
        if args.fixture:
            # ⛔ A FIXTURE IS NOT A LIVE READ. It is marked on stderr (so a caller can always
            # tell provenance) and it must carry a full 40-hex head, because the canonical
            # line claims `(<PR>, <full head sha>)` and `{"head": ""}` used to print
            # `READY-FOR-OWNER(1, )`.
            print(f"FIXTURE-MODE({args.fixture})", file=sys.stderr)
            probe = probe_from_dict(json.loads(Path(args.fixture).read_text()))
            if not re.fullmatch(r"[0-9a-f]{40}", probe.head or ""):
                raise ReadError(
                    f"a --fixture probe must carry a full 40-hex head; got {probe.head!r}"
                )
        else:
            probe = collect_probe(args.pr, args.repo, workdir)
        # ⛔ INSIDE the try: `evaluate()`, `render()` and `json.dumps` were OUTSIDE it, so a
        # crash in a condition (or in a `--fixture` payload whose comment `id` is not an int)
        # still printed a traceback and NO verdict line — the very shape the handler exists to
        # make impossible.
        verdict = evaluate(probe)
        if args.json:
            payload = json.dumps(verdict.to_dict(), indent=2, sort_keys=True)
        else:
            payload = render(verdict)
    except RateLimited as exc:
        # ⛔ The contract is "exactly one of READY-FOR-OWNER / NOT-READY", and a rate limit
        # used to print an EMPTY stdout — a third shape a caller grepping the line could not
        # parse. Every condition is unread, so every condition failed.
        print(f"GH-RATE-LIMITED({exc.endpoint})", file=sys.stderr)
        print(_failure_output(args, "gh-rate-limited"))
        return 1
    except Exception as exc:  # the OUTPUT CONTRACT outranks the exception
        # ⛔ A read that is not a verdict must never become a traceback with NO line. Every
        # reader is meant to convert its own failure, but this is the backstop that makes
        # "exactly one of READY-FOR-OWNER / NOT-READY" true even for a case nobody foresaw
        # (a payload whose `head` is a string rather than an object, a vendor schema change).
        print(f"READY-GATE-ERROR({type(exc).__name__}: {exc})", file=sys.stderr)
        print(_failure_output(args, f"error:{type(exc).__name__}"))
        return 1
    print(payload)
    return 0 if verdict.ready else 1


def _failure_output(args: argparse.Namespace, reason: str) -> str:
    """The SAME output shape the caller asked for, on the failure path.

    A `--json` caller used to receive a bare text line here, which is a parse error rather
    than a verdict — the third-shape problem in its last hiding place.
    """
    pr = args.pr if args.pr is not None else 0
    if getattr(args, "json", False):
        return json.dumps(
            {
                "pr": pr,
                "head": "",
                "ready": False,
                "failed": list(CONDITIONS),
                "line": f"NOT-READY({pr}, failed={','.join(CONDITIONS)})",
                "error": reason,
            },
            indent=2,
            sort_keys=True,
        )
    return f"NOT-READY({pr}, failed={','.join(CONDITIONS)})"


def _fixture_head_runs(data: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...] | None:
    """A fixture's run listing, or ``None`` when the fixture does not carry a READABLE one.

    The run listing and the emitter projection are what decide a merge-time context's
    ABSENCE, so the fixture door must be able to express both — a door that could not would
    force every absent-merge-time fixture into the UNATTRIBUTABLE arm and make the DEFERRED
    arm unreachable from ``--fixture``.

    ``None`` is the fail-closed "not read" arm — the value the live path sets when
    ``ci_verdict.collect_items`` refuses a truncated listing — and BOTH an omitted key and an
    explicit null land there. ⛔ Not the ``labels`` idiom: ``()`` is not a neutral default, it
    ASSERTS "read it, zero runs exist", which is the DEFERRED (PASS) arm, so a fixture that
    never spoke about attribution must not silently exercise the pass arm. A legitimate
    DEFERRED is spelled ``"head_runs": []``.

    The SHAPE is validated, not just presence: only a sequence whose every entry is a mapping
    THAT CARRIES a workflow ``path`` is a listing. ``"python-ci.yml"`` (a string, which would
    become a tuple of characters), a bare mapping, a listing with unreadable entries, and an
    entry without ``path`` (which ``_run_file`` maps to ``""``, matching no emitter) are all
    type-confused or partially-read surfaces; the live path refuses each of them, so the
    fixture door must not turn any of them into "read it, no run of the emitter exists".
    """
    if "head_runs" not in data or data["head_runs"] is None:
        return None
    raw = data["head_runs"]
    if not isinstance(raw, (list, tuple)):
        return None
    if not all(_readable_run(run) for run in raw):
        return None
    return tuple(raw)


def _fixture_merge_emit_files(data: Mapping[str, Any]) -> Mapping[str, tuple[str, ...]]:
    """A fixture's ``emitters`` projection, shape-validated like the run listing.

    The emitter map is what makes an absent merge-time context ATTRIBUTABLE, so a value
    that is not a list of workflow FILE NAMES is not a reading of it. Two plausible JSON
    slips are type-confused rather than merely empty:

    * ``{"python-ci-gate": "python-ci.yml"}`` (a bare string) becomes a tuple of
      CHARACTERS, so ``_run_file`` can never match it;
    * ``{"python-ci-gate": {"workflow": "python-ci.yml"}}`` (a bare mapping) becomes its
      KEYS.

    Neither matches an emitter, so each graded the absence ``DEFERRED`` — a PASS over an
    emitter that IS recorded and that DID run on the head (measured at ``caa4d7e6f``: the
    correct list yields ``missing:python-ci-gate``/FAIL, both slips yield PASS). The live
    path cannot produce either shape (``RequiredSet.emit_workflows`` yields tuples), so
    this is the fixture door — and the door must not turn a type-confused value into the
    pass arm.

    A malformed value collapses to ``()``, which `_merge_time_absence` reads as
    **UNATTRIBUTABLE** (FAIL), never as "read it, no emitter". An OMITTED or null
    projection stays ``{}`` — also UNATTRIBUTABLE for any required merge-time name — so
    every unreadable shape fails closed.
    """
    raw = data.get("merge_emit_files")
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for name, files in raw.items():
        if (
            isinstance(files, (list, tuple))
            and files
            and all(isinstance(f, str) and f for f in files)
        ):
            out[str(name)] = tuple(files)
        else:
            out[str(name)] = ()
    return out


def _fixture_workflow_files(data: Mapping[str, Any]) -> tuple[str, ...] | None:
    """A fixture's protected-ref workflow listing, or ``None`` when it did not state one.

    ⛔ SAME DOCTRINE AS `_fixture_head_runs` (#11175): the listing decides whether a
    recorded `emitters` spelling still exists, and a spelling that matches no workflow file
    is UNATTRIBUTABLE (FAIL) — while ``()` would ASSERT "read it, the ref has no workflow
    files", a measurement a fixture that never spoke about it did not make. So an omitted
    key lands on the fail-closed ``None``, and a legitimate empty ref lists
    ``"base_workflow_files": []`` explicitly.

    Shape is validated, not just presence: a bare string would become a tuple of
    CHARACTERS, which matches no file name and would make every emitter look stale.
    """
    if "base_workflow_files" not in data or data["base_workflow_files"] is None:
        return None
    raw = data["base_workflow_files"]
    if isinstance(raw, (str, bytes, Mapping)) or not isinstance(raw, (list, tuple)):
        return None
    if not all(isinstance(f, str) and f for f in raw):
        return None
    return tuple(raw)


def probe_from_dict(data: Mapping[str, Any]) -> Probe:
    """Build a Probe from a fixture dict (used by tests and ``--fixture``)."""
    groups = data.get("groups")
    parsed_groups: tuple[ci_verdict.GroupVerdict, ...] | None = None
    if groups is not None:
        parsed_groups = tuple(
            ci_verdict.GroupVerdict(
                app=g.get("app", "github-actions"),
                workflow=g.get("workflow"),
                workflow_key=g.get("workflow_key"),
                job=g["job"],
                state=g["state"],
                check_id=g.get("check_id"),
                status=g["status"],
                conclusion=g.get("conclusion"),
                details_url=g.get("details_url", ""),
                started_at=g.get("started_at"),
                voided_red=bool(g.get("voided_red", False)),
            )
            for g in groups
        )
    now = _parse_instant(str(data.get("now") or "")) or datetime.now(UTC)
    return Probe(
        pr=int(data["pr"]),
        repo=str(data.get("repo", DEFAULT_REPO)),
        head=str(data.get("head", "")),
        base=str(data.get("base", "main")),
        # None (absent or explicit null) is "not read" — the fail-closed default — so a
        # fixture that omits `is_draft`/`merged` cannot accidentally read as not-a-draft.
        # ⛔ `_tri_bool`, not `bool(...)`: `bool("false")` is `True`, which INVENTED a boolean
        # from a value that is not one. These three are the only tri-state fields a fixture can
        # set, and `head_stable` graded by `is False` was the one that failed OPEN (see
        # `_tri_bool`). A non-bool now reads as None = "not read", which fails closed.
        is_draft=_tri_bool(data.get("is_draft")),
        mergeable=str(data.get("mergeable", "UNKNOWN")),
        merge_state=str(data.get("merge_state", "")),
        head_committed_at=str(data.get("head_committed_at", "")),
        body=str(data.get("body", "")),
        # A fixture that omits `labels` means "no labels"; an explicit null means the
        # READ was unreadable. The live collector is the authority on which happened.
        labels=(
            None if "labels" in data and data["labels"] is None else tuple(data.get("labels") or ())
        ),
        labels_raw=str(data.get("labels_raw", "")),
        pr_state=str(data.get("pr_state", "open")),
        merged=_tri_bool(data.get("merged")),
        head_stable=_tri_bool(data.get("head_stable")),
        tree=data.get("tree"),
        tree_raw=str(data.get("tree_raw", "")),
        groups=parsed_groups,
        groups_raw=str(data.get("groups_raw", "")),
        head_runs=_fixture_head_runs(data),
        merge_emit_files=_fixture_merge_emit_files(data),
        base_workflow_files=_fixture_workflow_files(data),
        required=None if data.get("required") is None else tuple(data["required"]),
        # Same absent-vs-null idiom as `labels` above: a fixture that OMITS the key is a
        # pre-split fixture whose flat set was all entry checks (so `()` is faithful),
        # while an explicit null is "the READ was unreadable" and must refuse.
        required_deferred=(
            None
            if "required_deferred" in data and data["required_deferred"] is None
            else tuple(data.get("required_deferred") or ())
        ),
        required_config_error=str(data.get("required_config_error") or ""),
        required_raw=str(data.get("required_raw", "")),
        review_token=data.get("review_token"),
        review_exit=data.get("review_exit"),
        review_raw=str(data.get("review_raw", "")),
        review_verdict=data.get("review_verdict"),
        touched=None if data.get("touched") is None else tuple(data["touched"]),
        touched_raw=str(data.get("touched_raw", "")),
        union=None if data.get("union") is None else tuple(data["union"]),
        union_raw=str(data.get("union_raw", "")),
        union_unreadable=bool(data.get("union_unreadable", False)),
        uncovered=None if data.get("uncovered") is None else tuple(data["uncovered"]),
        coverage_raw=str(data.get("coverage_raw", "")),
        comments=None if data.get("comments") is None else tuple(data["comments"]),
        comments_raw=str(data.get("comments_raw", "")),
        now=now,
    )


if __name__ == "__main__":
    raise SystemExit(main())
