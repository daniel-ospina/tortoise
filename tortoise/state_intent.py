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

#: The BASE forms of the same words, which is what do-support selects
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

#: The trailing RECENCY adverbs `wh_whole_question` admits (#14033). CLOSED, and
#: the membership rule is that the adverb names NO boundary: it says how fresh the
#: caller wants the answer, which every returned row's own ``endedAt`` states, so
#: nothing is dropped by serving the whole population. `last quarter`, `in Q3` and
#: `this week` are NOT members on purpose — they name a window the read cannot
#: apply, and admitting one would be the silently-dropped-constraint defect this
#: table's history is mostly about.
_RECENCY_TAIL = r"(?:\s+(?:recently|lately|so far|to date))?"

#: ENDING-question shapes. ⛔ EVERY SHAPE IS ANCHORED, and that is the lesson of
#: three review rounds. A lexical approximation of "the ending is the question's
#: predicate" leaks: the first cut matched bare words ("what is the end date"),
#: the second matched a wh-word near a participle ("what is our abandoned cart
#: recovery rate", `which archived report has the Q3 numbers`), and the third
#: matched a copula ANYWHERE in the sentence ("what happens when an order is
#: cancelled", `what features are deprecated in v3`) — every one of those is a
#: content question and every one of them was answered with the whole ended
#: population instead of matching rows.
#:
#: The shapes below therefore each fix the grammatical role of the ending word,
#: and none of them matches a word that is merely PRESENT:
#:
#: * ``existence`` — "has anything we relied on BEEN withdrawn",
#:   "have any claims been retracted". An auxiliary plus an existential quantifier
#:   is a question about whether such a record EXISTS; the relative clause between
#:   them is the thing asked about, and it is the only place a window is allowed.
#:   ⛔ THE AUXILIARY IS MANDATORY, and that is the ATTRIBUTIVE discriminator
#:   (round-4 review, P1). An ending word that MODIFIES a following noun is not a
#:   predicate — `have any archived reports been published` and
#:   `do we have any archived reports` are content lookups in which `archived` is
#:   a modifier. With an OPTIONAL auxiliary the participle could sit straight
#:   after the quantifier, where it can only be attributive, and all of those
#:   were answered with the whole ended population. Requiring
#:   `been|already|ever` immediately before the participle is the predicative
#:   test: the participle is then the head of the verb phrase, not a modifier of
#:   the noun that follows the quantifier.
#:   ⛔ AND THE PARTICIPLE MUST END THE QUESTION (round-5 review, P1). This was
#:   the one ending shape with no tail anchor, so a caller's appended CONSTRAINT
#:   was silently dropped: `have any claims been retracted in Q3`, `have any of
#:   our bookings been cancelled by the venue` and `has anything we promised been
#:   withdrawn by legal` all selected the WHOLE ended population and served it as
#:   if the narrowing had been applied. Every other shape is anchored at the tail
#:   for exactly this reason, and the same fix is a window-preserving one — the
#:   middle window is the relative clause and stays.
#: * ``wh_whole_question`` — the WHOLE question is "what (has been) <participle>":
#:   "what ended", "what has been withdrawn". Anchored at both ends, so a trailing
#:   constraint (`what is the archived pricing document`) cannot reach it.
#: * ``which_predicate`` — the wh-word's OWN predicate: "which claims were
#:   archived". Anchored at the start and requiring the auxiliary IMMEDIATELY
#:   after the wh-phrase's HEAD NOUN, so `which archived report has the Q3
#:   numbers` (an attributive use) cannot.
#:   ⛔ AND ANCHORED AT THE END (round-4 review, P1), for the same reason: with
#:   nothing required after the participle, `which products have archived
#:   documentation` and `which units are cancelled in the export` fired, and in
#:   both the participle modifies the noun that follows it. Requiring the
#:   participle to END the question admits the wh-word's own predicate ("which
#:   claims were archived") and refuses the attributive use.
#:   ⛔ AND THE WINDOW IS NOT FREE — THE ADJACENCY IS THE GUARD (this lane, on
#:   the round-5 review's open judgement call). Round 4 bounded the window to
#:   30 characters while this paragraph already claimed the auxiliary followed
#:   "immediately"; 30 characters is not "immediately", and a bounded-FREE
#:   window admits a whole clause. Measured on the pre-fix module, all three
#:   routed as ``which_predicate`` and were served the WHOLE ended population
#:   of the family — the caller's head noun, and with it the question, dropped:
#:   `which vendor said our commitments were archived`, `which report says the
#:   product was deprecated`, `which notice confirmed the account was
#:   cancelled`. In each, the ending participle belongs to a SUBORDINATE clause
#:   whose own finite verb (`said`, `says`, `confirmed`) sits where a head
#:   noun's determiner must be — so the shape now requires the head noun to be
#:   ADJACENT to the auxiliary, behind at most a partitive (`of`) and a
#:   determiner, and every word the question turns on is the wh-word's own
#:   subject again.
#: * ``do_support`` — "what did WE <base verb>", "did we retire …". Anchored, and
#:   the base verb must follow the pronoun IMMEDIATELY — a window let
#:   `what do we do when we cancel a subscription` (a how-to question) through.
#:   ⛔ THE AUXILIARY MUST BE the PAST ``did`` (round-5 review, P1), and that is
#:   the same anti-signal the round-4 note above applies to the participle: the
#:   base verb sitting immediately after the pronoun is not enough, because
#:   `do|does` + we + a base ending verb is also the affirmative PRESENT-HABITUAL
#:   form. `do we archive customer emails`, `do we cancel subscriptions
#:   automatically`, `do we archive old reports` and `do we retract statements
#:   when challenged` are policy questions about what we do, and all four were
#:   answered with the whole ended population — on the default SDK, MCP and
#:   hosted `/search` surfaces, none of which can switch the route off. The PAST
#:   auxiliary is what makes the sentence a question about records that ended,
#:   so the shape requires it. The recall this gives up is stated below rather
#:   than discovered: a present-tense set question (`do we ever archive plans`) —
#:   which `ever` makes a FREQUENCY question — keeps the relevance read.
#:
#: ⛔ NOTHING IS SPLIT OFF THE QUERY, and a first cut that did is recorded here
#: because it is the shape a reader would otherwise re-add. It matched each shape
#: against ``text.split(",")[0]`` on the theory that *"an ending asserted in a
#: clause the caller tacked on does not carry the question"*. MEASURED (review
#: P2-5), the split can never REFUSE anything: the three shapes anchored at the
#: end (`wh_whole_question`, and the two noun shapes) are already blocked by any
#: trailing clause whether or not it is split off — `what is the payment terms
#: clause, given the contract was archived` keeps the relevance read with or
#: without it — and the three that are not anchored at the end (`existence`,
#: `which_predicate`, `do_support`) match the whole string exactly as they match
#: its first clause. What it CAN do is ADMIT, and what it admitted was the mirror
#: defect: a CONSTRAINT the caller appended after a comma is silently dropped, so
#: `current commitments, in EMEA` routed while `current commitments in EMEA` did
#: not — precisely what ``_COMMITMENT_SHAPES`` says must never happen.
#:
#: So the WHOLE query is matched. The cost is stated rather than discovered:
#: `what ended, and what replaced it` keeps the relevance read, because only the
#: whole question can be the question.
#:
#: THE POINT IS PRECISION, NOT RECALL — a misroute answers a content question
#: with a population, which is the defect this whole change exists to remove, so
#: where a shape could go either way it does not fire. The phrasings that
#: therefore do NOT reach this read are stated rather than discovered:
#:
#: * "which of our withdrawn claims affected pricing" (#14033's own fourth row) —
#:   the ending word MODIFIES the wh-phrase's head noun and the question's own
#:   predicate is "affected pricing", so the population would serve the set with
#:   the caller's question dropped. It keeps the relevance read AND carries the
#:   missed-cue notice below, which is the surface that makes the difference
#:   visible instead of silent.
#: * "what commitments do we have" / "what are the commitments" — a commitment
#:   noun that does not END the question or is not preceded by a set determiner;
#:   "current commitments", "our commitments" and "what are we committed to" do
#:   route.
#: * "show me the retired onboarding flow" / "list our commitments" — an
#:   imperative over a NOUN PHRASE, which was measured as a hijack: `show me the
#:   retired onboarding flow` is a lookup for ONE record. ⛔ The imperative over a
#:   FREE RELATIVE CLAUSE (`list what we retired`) IS a set question and does
#:   route — see `imperative_what`; the discriminator is the clause, not the
#:   imperative frame. The same frame with a SET NOUN (`list our commitments`,
#:   `show me all the commitments`) is the recall the start anchor gives up, and
#:   it is named here so it is a decision and not a gap.
#: * "do we ever archive plans" / "do we archive customer emails" — the
#:   PRESENT-habitual frame, which `do_support` refuses on purpose (round-5
#:   review, P1): it asks what we do, not which records ended.
#: * "have any claims been retracted in Q3" / "has anything been withdrawn by
#:   legal" / "which claims were retracted by legal" — an appended CONSTRAINT,
#:   refused by every shape's tail anchor so the caller keeps the relevance read
#:   instead of having the narrowing dropped. These are the rows the missed-cue
#:   notice exists for: each one IS a state question, and none of them routes.
#:
#: ⛔ AND A STRONG CUE THAT DOES NOT FIRE IS NOT SILENT (#14033). The precision
#: rules above are what keep a content question out of the population, and the
#: cost they charge is a real state question that keeps the relevance read —
#: whose empty (or unrelated) answer is indistinguishable from the factual claim
#: "nothing has ended". So every shape below is ALSO emitted as a PREFIX — the
#: shape without its tail anchor — and a query that matches a prefix while
#: matching no shape is reported as ``StateQuestion.signal``. That is the whole
#: definition of "strong": a cue the recogniser WOULD have applied but for a
#: guard the population cannot honour, never "an ending word somewhere in the
#: sentence". The read path answers those queries with the relevance read it
#: gave them and carries :func:`missed_route_payload` on its sink, so the caller
#: can tell the two apart and ask again in a form that routes.

_TAIL_END = r"\s*[?.!]*$"

#: …and the tail a BLOCKED state question has INSTEAD of the end: punctuation, or
#: a FUNCTION WORD the caller appended (`by legal`, `in Q3`, `when challenged`).
#: ⛔ This is what keeps the notice off an ATTRIBUTIVE use — `is the archived
#: pricing document live` continues with a content noun, so it is not a refused
#: constraint and gets no notice — while still reporting every refused qualifier,
#: which is the whole point (#14033: the caller must be able to tell a state
#: question that did not fire from a genuine empty answer).
_BLOCKED_TAIL = (
    r"(?=\s*(?:[,;:—–]|[?.!]|$|\b(?:in|on|at|by|for|from|with|to|of|about|over|"
    r"against|regarding|concerning|toward|towards|and|or|but|so|yet|that|which|"
    r"when|where|who|if|as|than|because|while|after|before|during|since|until|"
    r"per|last|next|this|these|those|yesterday|today|tomorrow|recently|lately|"
    r"already|ever|now|still)\b))"
)

#: "has anything we relied on been withdrawn?" — auxiliary + existential
#: quantifier as the question's subject, the relative clause between them as the
#: thing asked about, and the participle as the predicate.
#: ⛔ THE AUXILIARY FORM IS MANDATORY (`been|already|ever` immediately before the
#: participle). That is the ATTRIBUTIVE discriminator (round-4 review, P1): an
#: ending word that MODIFIES the noun after the quantifier is not a predicate, so
#: `have any archived reports been published` and `do we have any archived
#: reports` are content lookups and are refused.
_EXISTENCE_PREFIX = (
    rf"\b(?:has|have|had)\s+(?:any|anything|someone|something|everything|anyone)"
    rf"\b[^?]{{0,60}}?\b(?:been|already|ever)\s+(?:{_ENDING_WORDS})\b"
)

#: "what ended" / "what has been withdrawn" / "what was retracted" — the WHOLE
#: question, anchored at the start and at the participle.
_WH_PREFIX = (
    rf"^what\s+(?:(?:is|are|was|were|has|have|had)\s+)?(?:been\s+)?"
    rf"(?:{_ENDING_WORDS})\b"
)

#: "which claims were archived?" / "which of our endpoints is deprecated?" — the
#: wh-word's OWN predicate.
#: ⛔ THE HEAD NOUN IS ADJACENT TO THE AUXILIARY. The window used to be a free 30
#: characters, which admits a subordinate clause: `which vendor said our
#: commitments were archived`, `which report says the product was deprecated` and
#: `which notice confirmed the account was cancelled` all routed and were answered
#: with the whole ended population. A head noun cannot be followed by its own
#: finite verb before the auxiliary, so the NP is bounded to an optional partitive
#: + determiner + ONE head noun — the same grammatical-role anchor `do_support`
#: uses, not a wider vocabulary.
#: The recall this gives up is stated rather than discovered: a MODIFIED head noun
#: (`which customer claims were archived`, `which older endpoints are deprecated`)
#: keeps the relevance read, because a modifier is the same slot the subordinate
#: clause occupies and the shape does not fire where it could go either way.
_WHICH_PREFIX = (
    rf"^which\s+(?:of\s+)?"
    rf"(?:(?:our|the|all|your|their|its|my|these|those)\s+)?"
    rf"[a-z]+\s+"                      # the wh-phrase's HEAD noun
    rf"(?:is|are|was|were|has|have|had)\s+"
    rf"(?:been\s+)?(?:{_ENDING_WORDS})\b"
)

#: "is the onboarding flow retired?" / "has the partner call been cancelled?" — a
#: YES/NO question whose OWN SUBJECT is the thing asked about (#14033).
#: ⛔ THE PARTICIPLE MUST END THE QUESTION, AND THE AUXILIARY MUST OPEN IT. Those
#: two anchors are what keep the content questions out: `which features are
#: deprecated in v3` does not open with an auxiliary, `what happens when an order
#: is cancelled` has its copula in a SUBORDINATE clause (the shape requires the
#: auxiliary as the FIRST word), and `is the archived pricing document live` has
#: the ending word ATTRIBUTIVE with the question's own predicate after it.
#: ⛔ AND THE PERFECT BRANCH REQUIRES `been|already|ever` IMMEDIATELY BEFORE THE
#: PARTICIPLE, which is the same attributive discriminator `existence` uses:
#: `have any customers cancelled this month` is a question about what customers
#: did this month, not about which records ended, and without this it would have
#: been reported as a missed state cue.
#: The recall given up is stated rather than discovered, and both examples are
#: MEASURED rather than imagined: a subject with a modifier clause inside it (`is the
#: onboarding flow, as agreed in March, retired`) keeps the relevance read, and so
#: does a subject longer than four words.
#: ⛔ THE SUBJECT IS A CONTIGUOUS NOUN PHRASE, NEVER A FREE WINDOW — and this is the
#: round-6 review's P0, measured. The first cut reused `existence`'s 60-character
#: `[^?]{1,60}?` window for the subject, which admits a whole CLAUSE, so an ending
#: participle belonging to a SUBORDINATE clause routed:
#:
#:   `is the export broken because the schema was deprecated`
#:   `is it a problem that the endpoint is deprecated`
#:   `is it true that our claims were archived`
#:   `is the report accurate given the data was archived`
#:   `is the vendor still engaged now the contract is archived`
#:   `is there any chance the release was cancelled`
#:
#: Every one is a content question, and every one was answered with the WHOLE ended
#: population on the default SDK / MCP / hosted `/search` surfaces. It is the same
#: class round 3 pinned for `what …` (`what happens when an order is cancelled`) and
#: round 4 pinned for `which …` (`which vendor said our commitments were archived`),
#: left open for yes/no questions.
#:
#: So the subject is bounded the way `which_predicate` bounds its head noun: an
#: optional determiner plus ONE to FOUR contiguous lowercase words. A clause needs a
#: SUBORDINATOR (`because`, `that`, `when`, `since`, `given`, `now`, `if`, …) and a
#: second finite verb, and neither fits inside a four-word noun phrase — so the
#: participle is the question's own predicate again, which is the whole claim. The
#: recall given up is stated rather than discovered: a subject with a modifier
#: clause inside it (`is the onboarding flow, as agreed in March, retired`) keeps the
#: relevance read, and so does a subject longer than four words.
_COPULA_SUBJECT = (
    r"(?:(?:the|our|my|its|their|these|those|this|that|all|your)\s+)?"
    r"[a-z][a-z'’-]*(?:\s+[a-z][a-z'’-]*){0,3}"
)
_COPULA_PREFIX = (
    rf"^(?:(?:is|are|was|were)\s+{_COPULA_SUBJECT}"
    rf"\s+(?:already\s+|ever\s+)?(?:{_ENDING_WORDS})\b"
    rf"|(?:has|have|had)\s+{_COPULA_SUBJECT}"
    rf"\s+(?:been\s+|already\s+|ever\s+)(?:{_ENDING_WORDS})\b)"
)

#: "list what we retired" / "show me what we archived" — an IMPERATIVE over a FREE
#: RELATIVE CLAUSE (#14033).
#: ⛔ THE DISCRIMINATOR IS THE CLAUSE, NOT THE IMPERATIVE FRAME. `list what we
#: retired` asks for the SET of records that ended; `show me the retired onboarding
#: flow` — the imperative this table already refuses — asks for ONE record, and its
#: object is a NOUN PHRASE. So the shape requires the relative clause `what we|i …`
#: immediately after the verb, end-anchored.
#: ⛔ AND THE VERB FORM IS THE ENDED-STATE ONE (`_ENDING_WORDS`, never the base
#: list): `list what we archive` is the PRESENT-HABITUAL frame `do_support` refuses,
#: for the same reason it refuses `do we archive customer emails` — it asks what we
#: DO, not which records ended.
_IMPERATIVE_PREFIX = (
    rf"^(?:list|show|enumerate)\s+(?:me\s+)?what\s+(?:we|i)\s+"
    rf"(?:(?:have|had|already|ever)\s+)?(?:{_ENDING_WORDS})\b"
)

_ENDING_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("existence", re.compile(_EXISTENCE_PREFIX + _TAIL_END)),
    ("wh_whole_question", re.compile(_WH_PREFIX + _RECENCY_TAIL + _TAIL_END)),
    ("which_predicate", re.compile(_WHICH_PREFIX + _TAIL_END)),
    ("is_copula", re.compile(_COPULA_PREFIX + _TAIL_END)),
    ("imperative_what", re.compile(_IMPERATIVE_PREFIX + _TAIL_END)),
    ("do_support", re.compile(
        rf"^(?:what\s+)?did\s+(?:we|i)\s+(?:{_ENDING_BASE})\b")),
)

#: THE MISSED-CUE SIGNALS — DERIVED from the shapes, never re-typed.
#:
#: A signal IS its shape with the END ANCHOR swapped for :data:`_BLOCKED_TAIL`: the
#: same cue, matched without requiring the question to stop there. That is what makes
#: "a strong cue that did not fire" a mechanical fact about ONE expression — a second
#: hand-written list is exactly how the first cut went silent on the phrasings it had
#: just been taught (round-6 review, P1: the `wh_whole_question` signal dropped the
#: RECENCY tail, so `what has ended recently in Q3` was neither routed nor reported,
#: while the description advertised `what has ended recently` as a route).
#:
#: ⛔ ``do_support`` HAS NO SIGNAL ROW because its pattern has no end anchor to swap:
#: `what did we archive by legal` still routes and still serves the population. That
#: asymmetry is pre-existing and deliberate (see its note above), so it is excluded by
#: CONSTRUCTION here rather than by somebody remembering to leave it out.
#:
#: ⛔ ``which_names_an_ending`` IS THE ONE HAND-WRITTEN ROW, and it is the ATTRIBUTIVE
#: case: the ending word immediately followed by the NOUN it modifies, inside a
#: ``which`` question. That is #14033's fourth row — `which of our withdrawn claims
#: affected pricing` — whose ending word is a modifier and whose own predicate
#: (`affected pricing`) the population cannot answer, so it keeps the relevance read
#: and must still say that it saw a cue.
#: ⛔ IT IS ANCHORED TO THE MODIFIER SLOT, NOT TO "an ending word near a wh-word"
#: (round-6 review, P1). The first cut was `^which\s+[^?]{0,60}?\b<ENDING>\b`, which
#: also matched an ending participle inside SOMEBODY ELSE'S clause — 16 of the pinned
#: ``NOT_SELECTED`` content rows then carried a notice whose advice ("drop the
#: qualifier") would have served them the whole ended population. Requiring the
#: participle to be immediately followed by a noun keeps the attributive row and
#: refuses the subordinate-clause class, which the shapes already report.
_ENDING_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    *(
        (name, re.compile(shape.pattern.replace(_TAIL_END, _BLOCKED_TAIL)))
        for name, shape in _ENDING_SHAPES
        if _TAIL_END in shape.pattern
    ),
    ("which_names_an_ending", re.compile(
        rf"^which\s+(?:of\s+)?"
        rf"(?:(?:our|the|all|your|their|its|my|these|those)\s+)?"
        rf"(?:{_ENDING_WORDS})\s+[a-z]")),
)

#: What a caller is told when a state cue was seen and no shape applied. ONE
#: string, owned here, so the read path, the MCP description and the routing test
#: cannot drift into describing the boundary differently — the drift #14033 is
#: about ("the feature exists but did not fire, and nothing says so").
MISSED_ROUTE_HINT = (
    "this question names an ENDING state, but no state-question shape applied to "
    "it, so it was answered by the RELEVANCE read — an empty or unrelated reply "
    "here is NOT evidence that nothing has ended. If the ENDING is what you are "
    "asking about, ask it as a whole question (\"what ended\", \"is <record> "
    "retired?\", \"list what we retired\") or as an unqualified set (\"which "
    "<records> were retracted\"); every row of the ended population states how it "
    "ended (status, successorId/successorName, stage+outcome, endedAt). If the "
    "QUALIFIER is what you are asking about (\"in v3\", \"by legal\", \"in "
    "Q3\"), this relevance read is the right one and the population cannot apply it."
)

#: The reasons a strong cue can be seen without the population being served.
#: CLOSED, because a caller branches on the value: ``shape`` is the guard refusing
#: the question (an appended constraint, an attributive participle), ``family`` is
#: a shape that DID fire on a family the ended population does not exist for (an
#: Event has no lifecycle — ``ended.py``'s own refusal).
MISSED_ROUTE_REASONS = ("shape", "family")


#: THE COMMITMENT-question shapes. The issue's own wording — *a commitment is a KIND,
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
#:
#: ⛔ AND THE NOUN SHAPES ARE ANCHORED AT THE START OF THE QUESTION (round-5
#: review, P2). Ending at the noun was only half the rule: with no subject guard,
#: a noun phrase inside somebody else's clause matched and the caller's actual
#: subject was thrown away — `what did the vendor say about our commitments`,
#: `what did legal advise about our commitments` and `what are the risks to our
#: commitments` were answered with OUR commitments (none of those is a question
#: this population can answer), and `the board discussed current commitments` is
#: a STATEMENT, not a request for a set. The shapes therefore admit the noun
#: phrase only as the WHOLE request — optionally introduced by the wh-word's own
#: copula ("what are our commitments") — which is the same start anchor
#: `which_predicate` and `do_support` already use. The recall given up is stated
#: below rather than discovered: an imperative (`list our commitments`, `show me
#: all the commitments`) keeps the relevance read, because the measured hijack
#: `show me the retired onboarding flow` is the same imperative frame asking for
#: ONE record.
#:
#: ⛔ TWO MORE RULES ON THE SET-QUESTION, both round-4 review (P1), and both are
#: the SAME class the paragraph above records — one word different:
#:
#: * the PRONOUN and the VERB are adjacent apart from ADVERBS. A free window let
#:   an intervening noun become the subject, so `what are our competitors
#:   committed to` and `what are our vendors committed to` were answered with OUR
#:   commitments. Adverbs still pass (`we currently committed`), because an
#:   adverb cannot be the subject.
#: * a PREPOSITIONAL TAIL is a CONSTRAINT, not the idiom. `committed to in EMEA`
#:   and `committed to the SOC2 audit` narrow the request, and the population
#:   cannot apply a narrowing — so they keep the relevance read rather than
#:   having it dropped in silence. The pinned Q1 spelling (`committed to —
#:   pricing, …`) is an APPOSITIVE elaboration, and `committed to and has
#:   anything been withdrawn` is a COMPOUND question, so the tail is admitted
#:   when what follows the verb is punctuation, a conjunction, or the end of the
#:   question — and refused when it is a content word. That is stated as an
#:   allow-list rather than a preposition list because `to` belongs to the idiom
#:   itself: a deny-list of prepositions would have refused the compound question
#:   on its `to and` (measured — this was the first cut's regression).
_COMMITMENT_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "what are we (currently) committed to" / "what have we committed to"
    ("committed_to", re.compile(
        r"\bwhat\b[^?]{0,16}?\b(?:are|have|had)\s+(?:we|our)\b"
        r"(?:\s+(?:[a-z]+ly|still|also|now|ever)){0,2}\s+"
        r"\bcommitt(?:ed|ing)\b"
        r"\s*(?:to\b)?"
        r"(?=\s*(?:[—–\-,.;:!?]|and\b|or\b|but\b|so\b|yet\b|nor\b|$))")),
    # "current commitments" / "live commitments" / "what are the live commitments"
    ("scoped_noun", re.compile(
        r"^(?:what\s+(?:are|is)\s+)?(?:the\s+)?"
        r"(?:current|live|active|open|outstanding)\s+commitments\s*[?.!]*$")),
    # "our commitments" / "all the commitments" / "what are our commitments"
    ("determined_noun", re.compile(
        r"^(?:what\s+(?:are|is)\s+)?(?:our|all)\b(?:\s+the)?\s+commitments\s*[?.!]*$")),
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
    #: The strong cue that was seen when NO shape fired (:data:`_ENDING_SIGNALS`),
    #: naming the shape whose guard refused the question. ``None`` when a shape
    #: fired, or when nothing state-like was seen at all. ⛔ It NEVER changes
    #: ``population``: a signal is a REPORT, not a route, which is what keeps the
    #: precision contract ("where a shape could go either way it does not fire")
    #: intact while removing the silence #14033 is about.
    signal: str | None = None
    #: The matched text of that signal, so a caller can say WHICH words it saw.
    signal_cue: str | None = field(default=None)


def detect_state_question(text: str | None) -> StateQuestion:
    """Map a query to the state population it asks about (:data:`POPULATIONS`).

    Deterministic and bounded — one normalisation plus a scan of six compiled
    patterns, no model call and no IO, so the hot read path pays microseconds
    and the mapping is unit-testable offline. A ``None``/empty/whitespace query
    yields ``population=None``: full-scan mode and ``ended_scope`` are safe.

    ``ENDED`` takes precedence over ``COMMITMENTS`` when BOTH tables match the
    same question: the ending is the more specific question, and the ended
    population is the one that states how each record ended — the reverse
    precedence would answer a withdrawal question with everything still standing,
    the exact failure #13558 was filed for.

    ⛔ That precedence is OBSERVABLE, and the test that pins it says how (review
    P2-4): a single-table example cannot falsify it — `which commitments were
    withdrawn` matches only ``which_predicate``, so swapping the two loops moves
    nothing. A COMPOUND question matches both (`what are we committed to and has
    anything been withdrawn` is ``committed_to`` *and* ``existence``), and there
    the ordering decides the answer.

    The whole query is scanned; nothing is split off it (see ``_ENDING_SHAPES``
    for the measured reason the first-clause split was removed).
    """
    if not text or not str(text).strip():
        return StateQuestion()
    t = " ".join(str(text).lower().split())
    for shape, pattern in _ENDING_SHAPES:
        m = pattern.search(t)
        if m:
            return StateQuestion(ENDED, shape, m.group(0))
    # ⛔ THE SIGNAL IS CHECKED BEFORE THE COMMITMENT SHAPES, and a commitment
    # shape still WINS THE POPULATION: a question that is both an ending and a
    # commitment question is answered by the ending (the precedence note in the
    # docstring — `what are we committed to and has anything been withdrawn` is
    # `committed_to` AND `existence`). A commitment question that merely CONTAINS
    # an ending word in somebody else's clause (`what did the vendor say about our
    # commitments`) matches no signal either — every signal is a shape's own
    # expression, plus the attributive `which` row.
    for signal, pattern in _ENDING_SIGNALS:
        m = pattern.search(t)
        if m:
            for shape, commitment_pattern in _COMMITMENT_SHAPES:
                m2 = commitment_pattern.search(t)
                if m2:
                    return StateQuestion(COMMITMENTS, shape, m2.group(0))
            return StateQuestion(signal=signal, signal_cue=m.group(0))
    for shape, pattern in _COMMITMENT_SHAPES:
        m = pattern.search(t)
        if m:
            return StateQuestion(COMMITMENTS, shape, m.group(0))
    return StateQuestion()


def missed_route_payload(state_question: StateQuestion, *,
                         reason: str = "shape") -> dict | None:
    """The NOTICE a caller receives when a state cue was seen and not applied.

    #14033: a state question that misses the cue table keeps the relevance read it
    always had — the right precision choice, because the population cannot apply
    the caller's narrowing or answer their predicate. What it must not keep is the
    SILENCE: an empty or unrelated relevance reply to a state question is
    indistinguishable from the factual claim "nothing has ended", and that
    indistinguishability is the defect this notice removes.

    ``None`` when there is nothing to report — no strong cue matched
    (:data:`_ENDING_SIGNALS`) for ``reason='shape'``, or no routed population for
    ``reason='family'`` — so an ordinary content query produces no payload at all,
    and every caller that has not opted into a sink stays byte-identical.
    """
    if reason not in MISSED_ROUTE_REASONS:
        raise ValueError(
            f"missed-route reason must be one of {MISSED_ROUTE_REASONS}, "
            f"got {reason!r}")
    if reason == "family":
        if not state_question.population:
            return None
        population = state_question.population
        cue = state_question.cue
    else:
        if not state_question.signal:
            return None
        population = ENDED
        cue = state_question.signal_cue
    return {
        "routed": False,
        "population": population,
        "reason": reason,
        "cue": cue,
        "hint": MISSED_ROUTE_HINT,
    }


__all__ = [
    "COMMITMENTS",
    "ENDED",
    "MISSED_ROUTE_HINT",
    "MISSED_ROUTE_REASONS",
    "POPULATIONS",
    "StateQuestion",
    "detect_state_question",
    "missed_route_payload",
]
