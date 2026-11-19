"""Which STATE population does this question ask about? (#13557, #13558).

WHY THIS MODULE EXISTS
----------------------
The capstone (#13038) measured two questions that the product could answer and
no question could reach:

* *"What are we currently committed to?"* — `search('current commitments',
  object)` returned **0 rows**, while `search('launch event', object)` returned
  the plan. The plan is live and carries its stage; it is unreachable from the
  question because the word "commitment" does not occur in its text (#13557).
* *"Has anything we relied on been withdrawn?"* — the ended read
  (`search(None, ended_scope=True)`) returned the ended records, but it only
  answered when called with **no text at all**, so the sentence a customer
  actually says never became that call; the default scope answered the question
  with two unrelated rows at score 0.0 (#13558).

Both failures are the same shape: **the answer is a state population, and the
question is text**. A text read can only answer a question about words. These
two questions are about the state of the graph — which records still stand, and
which have ended — so every word in them is a *selector of a population*, not a
term to rank rows by. Nothing in the text of "what are we committed to"
distinguishes the launch plan from the pricing decision; the fact that the plan
is a `core:plan` carrying a non-terminal stage does.

So this module is the missing **edge**, and it is deliberately the whole of it:
a deterministic, bounded, offline mapping from a QUESTION SHAPE to one of the
state populations the read path already owns —
:data:`COMMITMENTS` (``tortoise/commitments.py``) and :data:`ENDED`
(``tortoise/ended.py``). It decides nothing else.

WHAT IT DOES *NOT* DO
---------------------
- **It does not mint a vocabulary.** The populations are selected by the SAME
  determinants every other reader uses: the commitment kinds of ONTOLOGY §5
  (``strategy``/``plan``/``goal``/``target``) and the shared currency predicate
  (``live._terminal_expression`` / ``_terminal_excluded``). No status word, no
  slot literal and no visibility field is declared here, and nothing here can
  change what "current" or "ended" MEANS — it only chooses which of the two the
  question is about.
- **It does not label a fact.** Nothing in a row's content is ever classified:
  the cue table below maps *question shapes* ("has anything … been withdrawn"),
  never a noun-to-state assignment, so a note that says "we withdrew the claim"
  is still a note (#13128's rule: a phrase is not a kind).
- **It does not answer.** It returns a population name; the two modules above
  answer from the graph. There is no model call, no IO, no graph handle here —
  it is a pure function of the query string, so the routing table is testable
  offline and the read path pays two linear regex scans.
- **It does not decide the family.** ``point``/``object``/``subject`` all carry
  a lifecycle; an ``event`` has none (design §8). Whether a population exists
  for the caller's ``entity_type`` is the caller's question (the read path
  checks it against each population's own declared families), because a
  population answered for a family that has no ending is the false claim
  ``ended.py`` refuses.

WHY A QUESTION SHAPE AND NOT A KEYWORD
--------------------------------------
A keyword match alone would hijack ordinary content searches: ``search('the
meeting was cancelled')`` is a lookup for a note, not a request for the ended
population. So an ending population is selected only by the *aggregative
question shapes* below — a wh-word or an "anything/which" quantifier carrying an
ending word — which is exactly the shape a customer uses when they do not know
which record they are asking about. A query that names a record ("onboarding
flow retired") keeps the text read it always had.

⛔ AND THE WORD MUST BE THE ENDED-STATE FORM: ``ended``, not ``end``;
``withdrawn``, not ``withdraw``. The first cut matched the bare verbs and the bare
noun, and review measured what that costs: ``what is the end date of the
contract``, ``what front end framework do we use`` and ``what happens if I
withdraw consent`` all returned the whole ended population instead of matching
rows, and ``commitments due this week`` returned every live commitment with the
caller's own constraint dropped. A cue is therefore (a) a shape that ASKS FOR A
SET and (b) a word that names a state already reached. Both halves are pinned in
``tests/test_13557_13558_reachable_reads.py`` — the negative controls are the
measured misroutes, not invented ones.

DELIBERATE BOUNDARY — "what was planned and never happened?"
-----------------------------------------------------------
That is a plan-versus-occurrence question, and it is NOT routed here. The ended
population answers "what ended, and how"; it cannot answer "did it happen",
because a plan whose terminal stage is ``held``/``completed`` ended AND happened.
Answering the never-happened question with the ended population would return
occurrences as misses — a wrong answer that looks right, which is worse than the
0 rows it replaces. The occurrence test is ``planned_occurrence.
OCCURRENCE_EVENT_KINDS`` (#13128) and the question keeps its own issue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── the populations a question may select ───────────────────────────────────
#: The live commitment-state population — every record still standing whose KIND
#: is a commitment kind, with the stage it stands on (``tortoise/commitments.py``).
COMMITMENTS = "commitments"
#: The ended population — every record the shared currency predicate treats as
#: ended, with how each ended (``tortoise/ended.py``).
ENDED = "ended"

#: The closed set, mapped to the module that ANSWERS each population — the
#: population's one home. Declared as data so the docstring above, the detector,
#: the read path and the routing test cannot drift apart.
POPULATIONS: dict[str, str] = {
    # the live commitments — kind + stage
    COMMITMENTS: "tortoise.commitments",
    # the ended records and how each ended
    ENDED: "tortoise.ended",
}

# ── the cue table ───────────────────────────────────────────────────────────
#: The ENDING words of the model, as a spelling family.
#:
#: ⛔ EVERY MEMBER IS THE **ENDED-STATE** FORM (a past participle), never the bare
#: verb — and that is the whole guard, measured in review. The bare forms turned
#: ordinary content questions into the ended population, because "end",
#: "withdraw", "retract", "cancel" and "retire" are how English talks about
#: dates, boundaries and actions, not only about records: `what is the end date
#: of the contract`, `what front end framework do we use`,
#: `what happens if I withdraw consent` and `show me the retired onboarding flow`
#: all reached the ended read. A past participle describes a state the record HAS
#: REACHED (withdrawn, retired, cancelled, archived) — which is exactly the
#: question this population answers — while a bare verb asks about an ACTION, so
#: it is not this read. That is why `ended` is a cue and `end` is not.
#:
#: The set is ``live.TERMINAL_EXCLUDED_STATUSES`` rendered the way a customer says
#: it (``retracted``, ``superseded``, ``archived``, ``deprecated``) plus the
#: plain-English endings for one with no single status word (``withdrawn``,
#: ``retired``, ``abandoned``, ``cancelled``, ``ended``). ⛔ It is a *detector*
#: vocabulary, never a state vocabulary: nothing here is written to a row or
#: compared with one — the population itself is selected by the predicate
#: (``ended.py``). ``tests/test_13557_13558_reachable_reads.py`` holds it to
#: ``live.TERMINAL_EXCLUDED_STATUSES`` so the two cannot drift.
_ENDING_WORDS = (
    r"withdrawn"
    r"|retracted"
    r"|superseded"
    r"|deprecated"
    r"|retired"
    r"|abandoned"
    r"|archived"
    r"|cancell?ed"
    r"|ended"
)

#: ENDING-question shapes, ordered. Each is the shape a customer uses when they do
#: NOT know which record they are asking about; a query that names a record
#: keeps the text read. ``[^?]`` bounds the scan inside one question so a
#: multi-clause string cannot join an ending word to an unrelated "what".
#:
#: ⛔ THERE IS DELIBERATELY NO `list`/`show` SHAPE. It was measured in review as a
#: hijack — `show me the retired onboarding flow` is a lookup for ONE record, and
#: routing it returned the whole ended population instead. An imperative that
#: asks for a SET still reaches this read through the `what`/`which` shapes
#: ("list what we retired"), so dropping the shape costs no real question.
_ENDING_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "has anything we relied on been withdrawn?" / "did something get retired?"
    ("anything", re.compile(
        rf"\b(?:anything|something|everything)\b[^?]{{0,60}}?\b(?:{_ENDING_WORDS})\b")),
    # "what has been withdrawn?" / "what was retracted?" / "what ended?"
    ("what", re.compile(
        rf"\bwhat\b[^?]{{0,60}}?\b(?:{_ENDING_WORDS})\b")),
    # "which commitments were withdrawn?" / "which of these ended?"
    ("which", re.compile(
        rf"\bwhich\b[^?]{{0,60}}?\b(?:{_ENDING_WORDS})\b")),
    # "what did we archive?" — a bare VERB after "what did we", from a CLOSED list
    # and deliberately WITHOUT `end` (which would match "what did we end up
    # doing"). The base form is needed only here: an enumerate-question about the
    # ACT of ending, in the first person, is unambiguous in a way that
    # "what is the end date" is not.
    ("did_we", re.compile(
        r"\bwhat\s+did\s+we\s+(?:archive|retire|withdraw|retract|cancel"
        r"|supersede|deprecate|abandon)\b")),
)

#: COMMITMENT-question shapes. The issue's own wording — *a commitment is a KIND,
#: not a phrase* — so the cue is the shape that ASKS FOR THE SET, and the
#: population is then read by kind. The verb phrase is the one a customer uses
#: ("what are we committed to"); the noun needs a determiner that asks for the
#: set ("current commitments", "our commitments", "which commitments").
#:
#: ⛔ NOT THE BARE NOUN. ``\bcommitments?\b`` was measured in review as a hijack:
#: `commitments due this week` and `commitment letter template` are content
#: lookups, and routing them returned every live commitment with the caller's own
#: constraint silently dropped — the failure #13558 is about, inverted. A question
#: that names a FACT about a commitment keeps the relevance read.
_COMMITMENT_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "what are we currently committed to" / "what have we committed to"
    ("committed_to", re.compile(r"\bcommitt(?:ed|ing)\s+to\b")),
    # "current commitments" / "live commitments" / "outstanding commitments".
    # PLURAL on purpose: "our commitment to privacy" and "the current commitment
    # level" are fact lookups, and the set-question is the plural one.
    ("scoped_noun", re.compile(
        r"\b(?:current|live|active|open|outstanding)\s+commitments\b")),
    # "our commitments" / "all the commitments" / "which commitments" /
    # "what are our commitments" — a determiner that asks for the SET, and PLURAL
    # for the same reason as `scoped_noun` ("our commitment to privacy" is a
    # fact lookup, not a request for the population).
    ("determined_noun", re.compile(
        r"\b(?:our|all|which|what)\b[^?]{0,20}?\bcommitments\b")),
)


@dataclass(frozen=True)
class StateQuestion:
    """What state population a query asks about.

    ``population``: :data:`COMMITMENTS`, :data:`ENDED`, or ``None`` (no state
    question — the read keeps the relevance path it has always had, byte for
    byte). ``shape``: the cue-table row that matched, for diagnostics and for
    the routing test's table; ``None`` with a ``None`` population.
    ``cue``: the matched text, so a caller can say WHICH words selected the
    population instead of asserting one.
    """
    population: str | None = None
    shape: str | None = None
    cue: str | None = field(default=None)


def detect_state_question(text: str | None) -> StateQuestion:
    """Map a query to the state population it asks about (:data:`POPULATIONS`).

    Deterministic and bounded — one normalisation plus a scan of six compiled
    patterns, no model call and no IO, so the hot read path pays microseconds
    and the mapping is unit-testable offline. A ``None``/empty/whitespace query
    yields ``population=None``: full-scan mode and ``ended_scope`` are safe.

    ``ENDED`` takes precedence over ``COMMITMENTS`` when both apply ("which
    commitments were withdrawn?"): the ending is the more specific question, and
    the ended population is the one that states how each record ended. The
    reverse precedence would answer a withdrawal question with everything still
    standing — the exact failure #13558 was filed for.
    """
    if not text or not str(text).strip():
        return StateQuestion()
    t = " ".join(str(text).lower().split())
    for shape, pattern in _ENDING_SHAPES:
        m = pattern.search(t)
        if m:
            return StateQuestion(ENDED, shape, m.group(0))
    for shape, pattern in _COMMITMENT_SHAPES:
        m = pattern.search(t)
        if m:
            return StateQuestion(COMMITMENTS, shape, m.group(0))
    return StateQuestion()


__all__ = [
    "COMMITMENTS",
    "ENDED",
    "POPULATIONS",
    "StateQuestion",
    "detect_state_question",
]
