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
#: The ENDING words of the model, as a spelling family — every member in its
#: **ENDED-STATE** form (a past participle). The bare verbs ("end", "withdraw",
#: "retract", "cancel") are how English talks about dates, boundaries and
#: actions, and matching them was the first cut's measured P0.
#:
#: The set is ``live.TERMINAL_EXCLUDED_STATUSES`` as a customer says it
#: (``retracted``, ``superseded``, ``archived``, ``deprecated``) plus the
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

#: …and the BASE forms of the same words, which is what do-support selects
#: ("what did we retire"). A closed list with NO ``end``: "what did we end up
#: doing" is not an ending question.
_ENDING_BASE = (
    r"withdraw"
    r"|retract"
    r"|supersede"
    r"|deprecate"
    r"|retire"
    r"|abandon"
    r"|archive"
    r"|cancel"
)

#: The copulas and auxiliaries that put a participle in PREDICATE position. The
#: participle must follow one of these IMMEDIATELY (allowing "not"/"already"/"ever"
#: and the like to sit between), because that adjacency — not the word itself — is
#: what makes the word a predicate rather than a modifier.
_AUX = r"(?:been|being|be|is|are|was|were|got|gets|gotten)"

#: ENDING-question shapes. ⛔ THE SHAPE IS SYNTACTIC, NOT LEXICAL, and this is the
#: lesson of review round 2. A past participle in ATTRIBUTIVE position names a
#: content TOPIC, and a wh-word is not by itself a request for a set — so an
#: earlier cut that only required "a wh-word within N characters of an ending
#: word" routed `what is our abandoned cart recovery rate`,
#: `what's our refund policy for cancelled orders`,
#: `which archived report has the Q3 numbers` and
#: `what is the archived pricing document` into the ended population. The three
#: shapes below all require the ending word to be PREDICATED of something:
#:
#: * ``aux_participle`` — an auxiliary, then the participle. This is the shape of
#:   "has anything we relied on BEEN withdrawn", "which claims WERE archived",
#:   "what has BEEN superseded". It is also the commonest hijack-free form
#:   because a modifier can never be preceded by a copula — AND it carries a
#:   wh-word/quantifier, because a bare statement is not a question:
#:   `the meeting was cancelled` is a note lookup and must not be routed.
#: * ``predicate_tail`` — the participle ENDS the question, within 30 characters
#:   of a "what": "what ended", "what has been withdrawn", "list what we retired".
#:   A trailing constraint kills it ("what is the archived pricing document" does
#:   not end on the participle), which also bounds the window the earlier cut
#:   claimed to have bounded and had not.
#:
#: ⛔ AND THE SHAPES ARE MATCHED ON THE FIRST CLAUSE (split on a comma or a
#: semicolon), because an ending asserted in a SUBORDINATE clause is a fact the
#: caller mentioned, not the question they asked: `what is the payment terms
#: clause, given the contract was archived` is a lookup for the terms.
#: * ``do_support`` — "did/do/does WE|I … <base verb>": "what did we archive",
#:   "did we retire the onboarding flow". Base forms only (from
#:   :data:`_ENDING_BASE`) and FIRST PERSON only — "did the customer cancel their
#:   order" asks about someone else's action, which this population cannot answer.
#:
#: ⛔ THERE IS DELIBERATELY NO `list`/`show` SHAPE. It was measured in review as a
#: hijack — `show me the retired onboarding flow` is a lookup for ONE record, and
#: routing it returned the whole ended population. A set-asking imperative still
#: reaches this read through ``predicate_tail`` ("list what we retired").
_ENDING_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "has anything we relied on been withdrawn?" / "which claims were archived?"
    ("aux_participle", re.compile(
        rf"\b(?:anything|something|everything|what|which)\b[^?]{{0,60}}?\b{_AUX}"
        rf"\s+(?:not\s+|already\s+|ever\s+|also\s+|just\s+|still\s+"
        rf"|never\s+)?(?:{_ENDING_WORDS})\b")),
    # "what ended" / "what has been withdrawn" / "list what we retired"
    ("predicate_tail", re.compile(
        rf"\bwhat\b[^?]{{0,30}}?\b(?:{_ENDING_WORDS})\s*[?.!]*$")),
    # "what did we archive last quarter" / "did we retire the flow"
    ("do_support", re.compile(
        rf"\b(?:did|do|does)\s+(?:we|i)\b[^?]{{0,30}}?\b(?:{_ENDING_BASE})\b")),
)

#: COMMITMENT-question shapes. The issue's own wording — *a commitment is a KIND,
#: not a phrase* — so the cue is a shape that ASKS FOR THE SET, and the population
#: is then read by kind. Two rules make that a syntactic claim rather than a
#: lexical one (review round 2 measured the difference):
#:
#: * the noun is PLURAL — "our commitment to privacy" and "the current commitment
#:   level" are fact lookups, and the set-question is the plural one;
#: * the noun ENDS the question (or is followed only by punctuation), so a caller
#:   who added a CONSTRAINT ("commitments due this week", "current commitments in
#:   EMEA") keeps the relevance read instead of having that constraint silently
#:   dropped by a population it cannot apply.
#:
#: ⛔ NOT THE BARE NOUN, and not "what … commitments": `commitments due this week`,
#: `commitment letter template` and `what commitments did the vendor make in the
#: SLA` were all measured as hijacks (the last one answered with OUR commitments,
#: a wrong answer that looks right). The verb phrase is anchored to "what
#: are/have we|our …" for the same reason — `how much have we committed to the
#: SOC2 audit` is a scalar question, not a request for the population.
_COMMITMENT_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "what are we (currently) committed to" / "what have we committed to"
    ("committed_to", re.compile(
        r"\bwhat\b[^?]{0,16}?\b(?:are|have|had)\s+(?:we|our)\b[^?]{0,16}?"
        r"\bcommitt(?:ed|ing)\b")),
    # "current commitments" / "live commitments" / "outstanding commitments"
    ("scoped_noun", re.compile(
        r"\b(?:current|live|active|open|outstanding)\s+commitments\s*[?.!]*$")),
    # "our commitments" / "all the commitments" / "what are our commitments"
    ("determined_noun", re.compile(
        r"\b(?:our|all)\b(?:\s+the)?\s+commitments\s*[?.!]*$")),
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

    Both tables are matched against the FIRST CLAUSE of the query (see
    ``_ENDING_SHAPES``): a subordinate clause is a fact the caller mentioned, not
    the question they asked.
    """
    if not text or not str(text).strip():
        return StateQuestion()
    t = " ".join(str(text).lower().split())
    clause = t.split(",", 1)[0].split(";", 1)[0].strip()
    for shape, pattern in _ENDING_SHAPES:
        m = pattern.search(clause)
        if m:
            return StateQuestion(ENDED, shape, m.group(0))
    for shape, pattern in _COMMITMENT_SHAPES:
        m = pattern.search(clause)
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
