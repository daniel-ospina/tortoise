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
  offline and the read path pays a bounded number of linear regex scans over
  the shape tables — the order and the count have ONE home, on
  :func:`detect_state_question`, which owns the scan and states their size.
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
#: ⛔ THE RULE ADMITS MORE SPELLINGS THAN THE LIST DOES, and that is deliberate: this
#: is the set of recency adverbs the shape ROUTES on, because a route serves the whole
#: population and every admission is a decision. The boundary-free adverbs it does not
#: route are reported ONLY when their FIRST word is a `_BLOCKED_TAIL` member
#: (`previously`, `earlier`, `before` — unrouted and reported); one whose first word
#: is not (`most recently`, `just recently`) is unrouted AND SILENT, which
#: `_BLOCKED_TAIL`'s own note states as the limit of the closed list.
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
#:   were answered with the whole ended population. Requiring `been` immediately
#:   before the participle is the predicative test: the participle is then the head
#:   of the verb phrase, not a modifier of the noun that follows the quantifier.
#:   ⛔ AND IT IS `been` ALONE — an earlier cut of this paragraph said
#:   `been|already|ever`, and that form is the round-7 hijack this file now refuses
#:   (`already`/`ever` admit the ACTIVE reading); see the note below
#:   :data:`_EXISTENCE_WINDOW`, which is the one statement of the rule.
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
#: * "show me the retired onboarding flow" — an imperative over a NOUN PHRASE,
#:   which was measured as a hijack: it is a lookup for ONE record, so the
#:   population is the wrong answer. ⛔ The imperative over a FREE RELATIVE
#:   CLAUSE (`list what we retired`) IS a set question and does route — see
#:   `imperative_what`; the discriminator is the clause, not the imperative frame.
#:   The same frame with a SET NOUN (`list our commitments`, `show me all the
#:   commitments`) is the recall the start anchor gives up — a DIFFERENT class
#:   from the hijack above (there the imperative asks for a set and we answer it
#:   with the relevance read; here it asked for one record) — and it is named here
#:   so it is a decision and not a gap.
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
#: "nothing has ended". So every TAIL-ANCHORED shape below is ALSO emitted as a
#: PREFIX — the shape without its tail anchor — and a query that matches a prefix
#: while matching no shape is reported as ``StateQuestion.signal``. ⛔ NOT EVERY SHAPE
#: HAS ONE: `do_support`'s pattern carries no end anchor to swap, so it is excluded
#: by construction and has no signal row (see `_ENDING_SIGNALS`; the identity test
#: pins the same asymmetry). That is the whole
#: definition of "strong": a cue the recogniser WOULD have applied but for a
#: guard the population cannot honour, never "an ending word somewhere in the
#: sentence". The read path answers those queries with the relevance read it
#: gave them and carries :func:`missed_route_payload` on its sink, so the caller
#: can tell the two apart and ask again in a form that routes.
#:
#: ⛔ WHAT THE NOTICE COVERS, STATED SO IT IS NOT OVERREAD. It is the expression-level
#: complement of the shapes — a cue whose shape matched but whose GUARD refused it
#: (an appended qualifier from :data:`_BLOCKED_TAIL`) — plus FOUR named rows for the
#: recall the shapes give up on purpose: an attributive `which` phrase
#: (``which_names_an_ending``), the unguarded `existence` expression whose clause guard
#: refused it (``existence_unguarded``), a MODIFIED `which` head noun
#: (``which_modified_noun``) and an over-long copula subject
#: (``is_copula_long_subject``). It is NOT "every question that mentions an ending":
#: an over-long `has … been <participle>` subject and a qualifier outside the closed
#: tail list are still silent, and widening the notice to cover them means widening
#: this paragraph's claim first.

_TAIL_END = r"\s*[?.!]*$"

#: ⛔ THE PREPOSITION CLASS THIS MODULE'S CLOSED LISTS ARE BUILT FROM — ONE HOME.
#: Prepositions are a CLOSED class (the `ADP` tag in the universal tag set; roughly
#: fifty single-word English members, and the multi-word forms are excluded on purpose
#: because both consumers here match a qualifier's FIRST word only), so a curated list
#: is the right mechanism in a module that deliberately carries no POS tagger. But the
#: class was typed out TWICE — once in :data:`_BLOCKED_TAIL` (the tail every tail-anchored
#: shape swaps its end anchor for) and once in the `is_copula` subject guard — and the
#: two copies drifted twice, each time caught by review only AFTER the fact:
#:
#: * round-1 P1 — `is the export archived by the vendor` was SILENT (neither routed
#:   nor reported) while the bare-noun spelling `… retired by legal` WAS reported,
#:   because the guard knew `of` and nothing else;
#: * round-2 P2 — `during`, `until`, `as` and `than` were still silent after that
#:   fix, while the prose above the guard claimed the class was closed.
#:
#: A third hand-typed copy drifts a third time, so BOTH sites now interpolate this
#: tuple (:data:`_PREPOSITION_RE`), and the membership is pinned TWICE: the tuple is
#: asserted verbatim (so a member can neither vanish nor appear without the commit
#: saying why), and every member is then driven through the detector on both consumers
#: (so a member that stops reaching one of them reds). See
#: `tests/test_13557_13558_reachable_reads.py::test_every_preposition_the_module_declares_reports_the_qualifier_it_introduces`.
#: ⛔ THE DELIBERATE OMISSIONS ARE NAMED, so the departure reads as a decision rather
#: than an oversight: `after`, `before` and `since` are prepositions AND subordinators;
#: guard A refuses a subordinator inside the subject and cannot tell a tail from a
#: subject without bounding itself at the participle (measured to re-route the
#: round-16 hijack), so those three still SILENCE the `is_copula` shape and the notice
#: for them is genuinely still owed — stated on the guard-B note. `up`/`down`/`off`
#: are particles in the positions this module matches (`what ended up happening` is a
#: content query and must stay quiet), and `like`/`following` are ambiguous enough to
#: stay out of a list whose false positives land on content searches.
_PREPOSITIONS = (
    "about", "above", "across", "against", "along", "alongside", "amid",
    "amidst", "among", "amongst", "around", "as", "at", "behind", "below",
    "beneath", "beside", "besides", "between", "beyond", "by", "concerning",
    "despite", "during", "except", "for", "from", "in", "inside", "into",
    "near", "notwithstanding", "of", "on", "onto", "outside", "over", "past",
    "per", "regarding", "than", "through", "throughout", "till", "to", "toward",
    "towards", "under", "underneath", "unlike", "until", "upon", "via", "with",
    "within", "without",
)

#: :data:`_PREPOSITIONS` as a regex alternation, LONGEST FIRST so a member that is a
#: prefix of another (`toward`/`towards`) cannot win the alternation and then fail the
#: `\b` that follows it.
_PREPOSITION_RE = "|".join(sorted(_PREPOSITIONS, key=lambda w: (-len(w), w)))

#: ⛔ THE MEMBERS OF :data:`_PREPOSITIONS` THAT CAN ALSO HEAD A CLAUSE — and therefore may NOT
#: be treated as a bare qualifier inside the `is_copula` subject (re-cut review, P1, measured):
#: `during`, `until`, `as` and `than` introduce a second clause (`is the report as the board
#: archived`), so exempting them let the shape swallow a whole clause and serve the ended
#: population with the caller's own predicate dropped and NO notice — #14033's own defect in the
#: worst direction. Declared as a ROLE, not a second hand-typed list: the exemption is DERIVED
#: below, so a new member of :data:`_PREPOSITIONS` is covered by default and only a member that can
#: head a clause has to be named here.
_CLAUSE_HEADING_PREPOSITIONS = ("as", "during", "than", "until")

#: :data:`_PREPOSITIONS` MINUS the clause-heading members — the qualifier class the `is_copula`
#: subject guard may treat as one noun phrase. Longest-first for the same reason as
#: :data:`_PREPOSITION_RE`.
_QUALIFIER_PREPOSITION_RE = "|".join(
    sorted(
        (p for p in _PREPOSITIONS if p not in _CLAUSE_HEADING_PREPOSITIONS),
        key=lambda w: (-len(w), w),
    )
)

#: …and the tail a BLOCKED state question has INSTEAD of the end: punctuation, or
#: a FUNCTION WORD the caller appended (`by legal`, `in Q3`, `when challenged`).
#: ⛔ This is what keeps the notice off an ATTRIBUTIVE use — `is the archived
#: pricing document live` continues with a content noun, so it is not a refused
#: constraint and gets no notice — while reporting a qualifier drawn from the CLOSED
#: list below, which is the whole point (#14033: the caller must be able to tell a
#: state question that did not fire from a genuine empty answer).
#: ⛔ IT IS A LIST, NOT A PREDICATE ("every word that is not a noun" needs a POS
#: tagger), and the limit is stated rather than discovered: a qualifier outside it
#: (`most recently`, `only after the audit`) stays silent — measured, and both stand
#: outside the list because their FIRST word is not a member (`as yet` does not: `as`
#: is one, so it is REPORTED). The members are the ones a customer
#: actually appends to an ending question — prepositions that introduce a narrowing,
#: conjunctions, determiners, and the boundary-free time adverbs.
#: ⛔ THE PREPOSITION SEGMENT IS INTERPOLATED FROM :data:`_PREPOSITIONS`, not typed
#: again: it and the `is_copula` subject guard each carried their own copy of the class
#: and drifted twice (round-1 P1, round-2 P2). One home, two consumers, and a pin that
#: drives every member through the detector.
_BLOCKED_TAIL = (
    r"(?=\s*(?:[,;:—–]|[?.!]|$|\b(?:"
    + _PREPOSITION_RE
    + r"|and|or|but|so|yet|that|which|"
    r"when|where|who|if|because|while|after|before|since|"
    r"last|next|this|these|those|yesterday|today|tomorrow|recently|lately|"
    r"already|ever|now|still|previously|earlier|afterwards|once|then|anymore|"
    r"ago)\b))"
)

#: "has anything we relied on been withdrawn?" — auxiliary + existential
#: quantifier as the question's subject, the relative clause between them as the
#: thing asked about, and the participle as the predicate.
#: ⛔ THE AUXILIARY FORM IS MANDATORY (`been` immediately before the participle).
#: That is the ATTRIBUTIVE discriminator (round-4 review, P1): an ending word that
#: MODIFIES the noun after the quantifier is not a predicate, so `have any archived
#: reports been published` and `do we have any archived reports` are content
#: lookups and are refused.
#: ⛔ AND IT IS `been`, NOT `been|already|ever` (round-7 review, P1). `already` and
#: `ever` admit the ACTIVE reading — `have any customers ever cancelled` asks what
#: customers DID, not which records ended — and both routed to the whole ended
#: population. The leak PREDATES this change; it is the same active-versus-passive
#: discriminator as the `is_copula` perfect branch, so it is closed here rather than
#: left as a known hole in the table this change is repairing.
#: ⛔ AND THE WINDOW IS GUARDED AGAINST A CLAUSE (round-8 review, P1). `[^?]{0,60}?`
#: admits ANY clause between the quantifier and `been`, not only a relative one — so
#: an ending participle belonging to a SUBORDINATE clause routed, which is the class
#: round 6 rated P0 for `is_copula` and round 4 for `which_predicate`, left open in
#: the one shape that never got the guard:
#:
#:   `have any customers complained since the old plan has been cancelled`
#:   `have any users complained because the feature has been deprecated`
#:   `has anything changed since the old pricing has been deprecated`
#:
#: Each was answered with the WHOLE ended population and the caller's own predicate
#: (`complained`, `changed`) silently dropped. Four lookaheads close it: a SUBORDINATOR in
#: the window (or a FREE-RELATIVE PRONOUN, `why`/`how` included — round-17 review, P2, below), a
#: RELATIVIZER with a word before it, an ASYNDETIC second clause — the NEGATIVE
#: lookahead below that reads it as any determiner + noun followed by a finite verb and
#: `been` — or a PRONOUN subject with that same predicate shape. The unguarded
#: expression is kept as its own SIGNAL row below,
#: so a question that fails only on this guard is still REPORTED rather than silenced.
#:
#: ⛔ AND THE SECOND LOOKAHEAD WAS THREE TIMES WIDER THAN THAT CLASS (round-9 review,
#: P2). Its first cut refused `determiner + noun + ANY auxiliary` and called the
#: sequence "a second clause's subject and predicate" — but that sequence is also the
#: DOCUMENTED LEGITIMATE CONTENT of this window (":176 the relative clause between them
#: is the thing asked about, and it is the only place a window is allowed"), so it
#: refused the shape it guards. Measured: `has anything the board has approved been
#: superseded`, `has anything the team is tracking been withdrawn`, `have any features
#: the vendor has deprecated been archived` and four more were all refused and reported
#: as `existence_unguarded` — and NOTHING pinned the lookahead at all (removing it red
#: 0 rows across the whole corpus), which is the same defect this file repairs one
#: shape above: a widening whose only justification is a comment is a rule nothing
#: holds.
#:
#: Narrowed, because only `<aux> been` can supply the token the window requires
#: (:data:`_EXISTENCE_WINDOW` needs the literal `been`): as narrowed then, the class
#: it protected was exactly `<det> <noun> has|have|had been`, the asyndetic hijack
#: `have any users reported the vendor has been deprecated`. ⛔ THE CLASS IS WIDER
#: NOW, by the follow-up note below: the guard protects any FINITE VERB from a closed
#: list followed by an optional `not`/`have` and then `been` (18 spellings, incl. the
#: modal passive `will have been`), because bounding it to `has|have|had been` left
#: that class open. Both classes are pinned, and each in the direction it belongs.
#:
#: ⛔ RESIDUAL, STATED RATHER THAN LEFT TO BE DISCOVERED: a RELATIVE clause whose head
#: is preceded by a determiner and whose own verb is `has|have|had been` — `has
#: anything the committee has been given been archived` — still reports rather than
#: routes. That is strictly narrower than the class this replaces and is held by the
#: two pins above, so a later simplification that re-widens the lookahead reds them.
#: ⛔ AND THE SECOND CLAUSE'S VERB IS FOLLOWED BY `been`, NOT BY A CAPTURED VERB LIST
#: (round-9 review, P2 follow-up). Bounding the class to `has|have|had been` closed the
#: bare hijack and left the MODAL PASSIVE one open: measured on that head,
#: `have any users noticed the vendor will have been deprecated` and
#: `have any customers heard the tier could have been retired` both ROUTED to the whole
#: ended population and dropped the caller's own predicate (`noticed`, `heard`). The
#: window needs the literal `been`, so `been` is what the lookahead must require: any
#: finite verb — bare, modal, perfect, negated — followed by `(been)` is the second
#: clause's own passive, and the closed verb list was only ever a proxy for it. The
#: class is pinned in `NOT_SELECTED` + `NOTICE_ONLY` (`… the vendor will have been
#: deprecated`), so re-narrowing it to the bare auxiliaries reds a row.
#:
#: ⛔ AND THE SUBORDINATOR LIST OMITTED THE COMPLEMENTIZERS (round-14 review, P1).
#: `whether`, `once` and `whereas` introduce no relative clause on the head and were
#: simply absent, so the class this guard exists for routed through the one spelling
#: the list did not name:
#:
#:   `has anyone asked whether the old plan has been archived`
#:   `has anyone said that our launch plan has been withdrawn`
#:   `has anyone noted which endpoint has been deprecated`
#:
#: All three were answered with the WHOLE ended population and the caller's own
#: predicate (`asked`, `said`, `noted`) silently dropped — the fail-open round 8
#: closed for `since`/`because`, in the spelling the list did not carry. They are
#: pinned in `NOT_SELECTED` + `NOTICE_ONLY` so this cannot re-open quietly.
#:
#: ⛔ AND IT STOPPED TWO SPELLINGS SHORT (round-17 review, P2, measured). `why` and
#: `how` are free-relative complements like `whether`, and neither was a member:
#:
#:   `has anyone asked why the old plan has been archived`
#:   `has anyone asked how the old plan has been archived`
#:
#: Both ROUTED to the whole ended population with the caller's predicate (`asked`)
#: dropped. The one-word-NP sibling (`… why the data has been archived`) WAS caught,
#: which is why nothing reddened: it fails on the determiner+noun+finite verb+`been`
#: lookahead instead, and only a MULTI-word noun phrase (`old plan`) slips that shape.
#: So the hole was invisible to every pinned row, and the fix is the list, not the
#: other lookahead — a guard whose coverage depends on how many words the noun phrase
#: happens to have is a guard that fails open on the longer spelling. Both rows are
#: pinned in `NOT_SELECTED` + `NOTICE_ONLY` (they report as `existence_unguarded`).
#:
#: ⛔ THE RELATIVIZERS (`that|which|who|whom|whose`) ARE GUARDED, NOT LISTED, and the
#: difference is measured rather than stylistic: a relative clause on the head IS the
#: documented legitimate content of this window (`:176`), so
#: `has anything that the board approved been superseded` must keep ROUTING. What
#: makes the three rows above fail open is a WORD BEFORE the relativizer — the head
#: clause's own verb (`asked`, `noted`) — so the lookahead requires it, and the
#: quantifier's own relativizer, directly adjacent with no preceding word inside this
#: span, still routes. That row is pinned in the routing table, so a blanket widening
#: of this guard reds instead of quietly costing it.

_EXISTENCE_AUX = (
    # ⛔ THE CONTRACTED HEADS ARE ABSENT HERE ON PURPOSE, AND THAT IS A SHAPE-SIDE DECISION
    # (round-33 review, P2, measured): this constant is the head of `_EXISTENCE_PREFIX`, i.e. it
    # ROUTES, so widening it to `hasn't|haven't|hadn't` would move the routing surface. The
    # round-32 review measured what the omission costs on the NOTICE side — 30 of 30
    # `{hasn't,haven't,hadn't} x {anyone,anybody,someone,nobody,no one} x {reported the vendor has
    # been deprecated, reported it has been archived}` rows were SILENT while every uncontracted
    # twin reported `existence_unguarded` — so the contraction is admitted by the NOTICE arm
    # (`_EXISTENCE_HEAD_NOTICE` below), not by this one. Two homes for one class is what this
    # module keeps paying for, so the quantifier list is shared: `_EXISTENCE_QUANTIFIERS` is used
    # by both heads and neither re-types it.
    r"\b(?:has|have|had)"
)
_EXISTENCE_QUANTIFIERS = (
    # ⛔ AND THE QUANTIFIER LIST IS AN ENUMERATION, WHICH IS THE POINT (review, P3, measured —
    # pre-existing, and fixed here because this block is being rewritten and the class is
    # otherwise treated as closed): `somebody`, `everyone`, `everybody`, `nobody` and `nothing`
    # are the SAME frame, and their absence left one spelling SILENT where its synonym reported —
    # `has somebody reported the vendor has been deprecated` came back `population=None,
    # signal=None` while `has someone reported …` reported `existence_unguarded`. Measured
    # across 26 predications x 14 subjects x 19 auxiliaries.
    # ⛔ `anybody` AND THE TWO-WORD `no one` WERE STILL MISSING (round-14 review, P2, measured):
    # the round-13 widening added five spellings and left out `anybody`, the exact synonym of
    # the `anyone`/`somebody` pair it had just made report — so
    # `has anybody reported the vendor has been deprecated` was SILENT while
    # `has somebody reported …` reported. `no one` / `no-one` were silent for the same reason:
    # the gap consumes ONE token, so a two-word quantifier matched neither half. The same
    # defect the list was widened for, one spelling over — which is the argument for widening
    # by CLASS here rather than by the row that was just measured.
    r"(?:any|anything|anyone|anybody|someone|somebody|something|"
    # ⛔ AND "TWO SPELLINGS" IS A CLASS, NOT A COUNT (round-14 follow-up review, P3,
    # measured): the arm shipped as `no[\s-]one`, i.e. U+0020 and U+002D only, so the
    # typographic hyphens a customer's keyboard or a pasted document actually produce were
    # still SILENT — `has no‑one reported …` (U+2011), U+2013 and U+2014 all returned
    # `(None, None)` while the ASCII twin reported, and so did the un-hyphenated `noone`.
    # That is the same mistake the module already paid for on apostrophes, whose inventory
    # is an explicit codepoint class rather than "seven handsome codepoints": the separator
    # here is the same idea, `[\s\u2010-\u2014-]*` — any space, any dash in the
    # typographic block, or none at all (the `noone` misspelling).
    r"everything|everyone|everybody|nobody|nothing|no[\s\u2010-\u2014-]*one)\b"
)
_EXISTENCE_HEAD = _EXISTENCE_AUX + r"\s+" + _EXISTENCE_QUANTIFIERS
#: The NOTICE arm's head: the same two classes with the contracted auxiliary admitted. It is a
#: SIGNAL-side head, so it cannot route; keeping it derived means the quantifier list still has
#: one home (the round-32 review measured 30 of 30 contracted frames SILENT without it).
_EXISTENCE_HEAD_NOTICE = _EXISTENCE_AUX + r"(?:n['\u2019]t)?\s+" + _EXISTENCE_QUANTIFIERS
_EXISTENCE_ADVERB = (
    r"(?:[a-z]+ly|just|already|only|still|even|also|never|ever|now|yet|"
    # ⛔ THE `-ly` CLASS IS NOT THE ADVERB CLASS (round-24 review, P1, measured):
    # `very`, `quite`, `almost`, `just about`, `in fact` are adverbials that END IN
    # SOMETHING ELSE, so the first cut of this slot was blind to the commonest English
    # spelling — `have any users reported the vendor has very recently been deprecated`
    # still routed the whole ended population with the caller's predicate dropped and
    # `signal=None`, i.e. the round-23 defect one spelling over. Measured over 7 verbs x
    # 7 subjects x 12 spellings: 291 of 588 routed before this list was widened.
    r"very|quite|rather|almost|nearly|too|much|all|as|so|about|in|fact|practice|"
    r"effect|good|long|since|far|"
    # ⛔ AND THE LIST IS STILL AN ENUMERATION, WHICH IS THE POINT (round-25 review, P1,
    # measured): `often`, `always`, `sometimes`, `seldom`, `soon`, `again`, `twice`,
    # `then`, `no longer`, `to date`, `of late`, `at last` are adverbials in NO `-ly`
    # shape and were not named, so twelve of twelve still routed the whole ended
    # population with the caller's predicate dropped and `signal=None` — the same defect
    # one spelling over, for the third round running. The tokens below close the
    # spellings this review measured; the RESIDUAL is stated rather than called closed:
    # an adverbial this list does not name still routes, because telling an adverbial
    # from a noun without a lexicon is the limit `_COPULA_SUBJECT` already states.
    r"often|always|sometimes|seldom|rarely|soon|again|twice|then|longer|date|late|"
    # …and the MULTI-WORD adverbials need their own leading word in the gap (`no longer`,
    # `to date`, `of late`, `at last`): the gap consumes ONE token per iteration, so a
    # two-word adverbial matched neither half alone. These four join the slot for exactly
    # that reason; a two-word adverbial this list does not lead is the stated residual.
    r"last|first|no|to|of|at)"
)
_EXISTENCE_HELPER = (
    # ⛔ `not` IS POSITIONAL, NOT FIRST (round-25 review, P1, measured): the slot used to
    # accept `(?:not\s+)?` only at the FRONT of the gap, so `has still not been` and
    # `has recently not been` both routed — a negation after any adverbial re-opened the
    # hole. `have` joins the same gap alternation for the same reason (the modal passives
    # `will have recently been`, where `have` is not adjacent to `been`).
    # …and the REPETITION BOUND is 10, because the bound it REPLACES was effectively 10: the
    # old helper was TWO `{0,5}` runs (adverbs, optional `have`, adverbs), so any bound below
    # ten is a NARROWING of this guard, not a widening — and narrowing it is the dangerous
    # direction, because a question the guard no longer matches is ROUTED to the whole ended
    # population with the caller's predicate dropped and `signal=None` (the #14033 hijack).
    # Measured at the guard-regex level (so the 60-char window does not confound it): at
    # `{0,8}` a nine- and a ten-adverb question FAILED TO MATCH the guard while the previous
    # head matched both. ⚠️ END-TO-END THIS IS UNOBSERVABLE: the window dominates at that
    # length, so `detect_state_question` answers identically at both bounds (420 probes, 0
    # diffs) — the bound is a guard property, not a route claim:
    #     n=8  prev_refuses=True   {0,8}_refuses=True
    #     n=9  prev_refuses=True   {0,8}_refuses=False   <- the narrowing
    #     n=10 prev_refuses=True   {0,8}_refuses=False   <- the narrowing
    # The bound still keeps the window from swallowing a whole clause; it does not grade
    # English, and a bound chosen for "how many adverbials a person writes" is exactly the
    # spelling-blind count this guard has been bitten by in three consecutive rounds.
    rf"(?:[,\s]*(?:not|have|{_EXISTENCE_ADVERB})\b[,\s]+){{0,10}}been\b"
)
# ⛔ THE PASSIVE PERFECT WITH AN ADVERB (re-cut review, P1, measured): the window required
# `been` ADJACENT to the participle, so `have any claims been already retracted`,
# `has anything been already withdrawn`, `have any of our bookings been already cancelled` and
# `have any claims been ever retracted` came back `population=None, signal=None` while `main`
# routes every one of them to `ended` — #14033's own defect (an unrouted state question that
# is indistinguishable from "nothing has ended") reintroduced by a guard fixing a different
# spelling. The slot is ONE adverbial (the fixed set below, not the whole `_EXISTENCE_ADVERB`
# class: those are the ones a customer writes between the auxiliary and the participle, and a
# wider slot here would swallow a second clause). `have any reports already archived` — where
# `already` is the AUXILIARY and `archived` stays ATTRIBUTIVE — is `main`'s behaviour traded
# away deliberately and is pinned in NOT_SELECTED.
#: ⛔ AND THAT TRADE WAS SILENT, WHICH IS THE DEFECT ONE LEVEL DOWN (turn-3, P2, measured):
#: every row of that class came back `population=None, signal=None` where `main` routes it to
#: `ended`/`existence`, so a caller could not tell "no records ended" from "I saw your ending
#: cue and did not answer it" — #14033 duty (b). The DECISION is kept (nothing here routes
#: this class); the SILENCE is closed by :data:`_ALREADY_EVER_AUX`'s arm in
#: ``existence_unguarded``.
#: ⛔ TWO SLOTS, AND THE ASYMMETRY IS THE POINT (two rounds of review, both measured).
#: The WINDOW is RECALL: its slot is the narrow set a customer actually writes between the auxiliary
#: and the participle (`already|ever|recently`), because a wider slot there would let the window
#: swallow a second clause.
#: The two LOOKAHEADS are REFUSAL: they only ever ADD a refusal, so their slot is the WHOLE
#: :data:`_EXISTENCE_ADVERB` class. Giving them the narrow slot was a measured hole — `just` and
#: `only` are members of that class (so `{_EXISTENCE_HELPER}` already accepts them BEFORE `been`)
#: but were not in the narrow slot, so they blinded both lookaheads and
#: `have any users reported it has been just deprecated and been withdrawn` ROUTED the whole ended
#: population with the caller's predicate dropped and `signal=None`, while the `already` spelling of
#: the same string was refused. Refusal-side widening cannot create a route: it can only over-refuse,
#: which the canonical relative-clause rows below measure.
_EXISTENCE_ADVERB_SLOT = r"(?:(?:already|ever|recently)\s+)?"
#: The refusal-side slot: ANY adverbial the guard's own class knows, INCLUDING the multi-word ones.
#: ⛔ THE REPETITION IS THE POINT (round-3 review, P2, measured): the class carries the LEADING
#: words of four multi-word adverbials (`no longer`, `to date`, `of late`, `at last`) precisely so a
#: token-per-iteration gap can consume them, and a slot bounded to ONE token could not — measured,
#: `have any users reported it has been no longer deprecated and been withdrawn` still ROUTED with
#: the caller's predicate dropped while the same adverbial BEFORE `been` was refused. The bound is
#: 3 (a multi-word adverbial is 2 tokens, plus headroom), and it is REFUSAL-SIDE: it can only add a
#: refusal, never a route.
#: ⛔ THE BOUND IS NOT 3 (round-5 review, P2, measured): TWO multi-word adverbials in a row need
#: FOUR tokens, and `{0,3}` refused at most three — so
#: `have any reported it has been in fact only just deprecated and been withdrawn` ROUTED the whole
#: ended population with the caller's predicate dropped and `signal=None` while its 3-token twin was
#: refused, the same defect one token higher. The bound is now the `_EXISTENCE_HELPER` gap's own
#: `{0,8}`: a count bound one short of the class it carries is a spelling this guard is blind to,
#: and this is the fourth consecutive round that lesson has been paid for.
#: ⛔ AND THE WIDENING IT PAID FOR OVER-REFUSED THE SAME SPELLING INSIDE A RELATIVE CLAUSE
#: (round-6 review, P2, measured — this lane's own regression, caught by a fresh reviewer): with
#: `{0,8}` a RELATIVE clause whose own participle is an ENDING word was refused instead of routed,
#: because the lookahead's job is to spot a SECOND CLAUSE and a relative clause's participle can
#: carry the same adverb run. Each of these routed on `main` AND on the pre-change head, and only
#: the TOKEN COUNT separated them from the asyndetic hijack they were meant to be refused with:
#:   relative (must ROUTE): `has anything that has been only just very recently deprecated been
#:                           withdrawn` / `… in fact only just superseded been withdrawn` /
#:                          `… in fact only just superseded by legal been withdrawn`
#:   asyndetic (must REFUSE): `have any reported it has been in fact only just deprecated and been
#:                            withdrawn`
#: Both consumers therefore carry `(?!\s*been\b)` after the ending word: the ending is the RELATIVE
#: clause's when that clause CONTINUES into the question's own `been …`, and then it is not the
#: question's predicate. Both directions are pinned in `SELECTED` / `NOT_SELECTED`.
#: ⛔ AND THE AGENT ARM WAS A MISTAKE (round-7 review, P2, measured — this lane's own regression,
#: caught within the same hour): tolerating `by` as well made both lookaheads blind whenever the
#: reported clause's participle took an agent, so the asyndetic hijack this guard exists for ROUTED
#: again — `have any users reported it has been deprecated by legal been withdrawn`, and its bare-
#: noun and clitic twins, all returned `ended` with `signal=None` (the silent, predicate-dropping
#: direction). A `by` phrase cannot separate the two readings by tokens alone: the RELATIVE row
#: `… that has been … superseded by legal been withdrawn` and the ASYNDETIC row
#: `… it has been deprecated by legal been withdrawn` differ only by a relativizer, which lives
#: OUTSIDE this slot. So the `by` tolerance is WITHDRAWN and its cost is stated rather than hidden:
#: a relative clause whose own participle takes an agent is refused AND REPORTED (a recall loss in
#: the module's preferred direction), pinned in `NOT_SELECTED` + `NOTICE_ONLY`;
#: ⛔ `already`/`ever` AS THE AUXILIARY IS A REFUSED CUE, AND A REFUSAL NOBODY REPORTS IS
#: #14033's OWN DEFECT (turn-3, P2, measured at `34b339c75d` vs `main` `70fbd1a27d`). This
#: branch made the existence WINDOW's auxiliary the literal `been` (the round-7 active-reading
#: decision — KEPT, not reversed), and that left the spellings where `already`/`ever` IS the
#: auxiliary matching nothing at all: `have any reports already archived`, `have any reports ever
#: archived`, `have any claims already retracted`, `have any reports already retracted`,
#: `have any reports ever withdrawn` and `have any customers ever cancelled` all returned
#: `population=None, signal=None` while `main` routes every one of them to `ended`/`existence`.
#: `main`'s answer was WRONG (the active reading) and at least VISIBLE; silence is
#: indistinguishable from "nothing has ended", which is duty (b) exactly. This constant feeds
#: the `existence_unguarded` SIGNAL only — a signal is computed after every shape has declined
#: and can never create a route, so the round-7 refusal is untouched — though it can change
#: WHICH notice a compound question gets, because the signal rows are ordered (see ARM C).
_ALREADY_EVER_AUX = r"(?:already|ever)"
_EXISTENCE_REFUSAL_ADVERB_SLOT = rf"(?:(?:{_EXISTENCE_ADVERB})\s+){{0,8}}"
#: ⛔ A CONTRACTED AUXILIARY CARRIES ITS NEGATION INSIDE THE WORD (round-26 review, P1; round-28
#: at `feb2c740bb`, P1, measured — the SAME class, one guard over). One home, THREE consumers:
#: both `_EXISTENCE_GUARD` lookaheads and `_COPULA_SUBJECT`'s second-verb lookahead. The third
#: copy was missing entirely, and that is what the round-28 review measured: the contracted
#: asyndetic twins ROUTED through the shape `detect_state_question` evaluates FIRST
#: (`_ENDING_SHAPES` before `_ENDING_SIGNALS`), with the caller's own verb dropped and
#: `signal=None` —
#:
#:   `has anyone reported v3 hasn't been deprecated`   <- routed, signal None
#:   `has anyone complained billing hasn't been deprecated`
#:
#: Both are refused now. Measured: 80 of 150 templated `<head> <subject> <contraction> been
#: <ending>` probes routed before this constant was interpolated here, 0 after, and 0 pinned
#: rows move. ⛔ AND THE APOSTROPHE IS TWO CHARACTERS, not one: `hasn't` and `hasn’t` are the
#: same word, and a spelling that accepted only ASCII would keep the class open for every
#: phone keyboard that types the typographic quote.
_CONTRACTED_AUX = (
    r"hasn['’]t|hadn['’]t|haven['’]t|isn['’]t|aren['’]t|wasn['’]t|weren['’]t|"
    r"doesn['’]t|didn['’]t|don['’]t|won['’]t|wouldn['’]t|can['’]t|couldn['’]t|"
    r"mightn['’]t|mustn['’]t|shouldn['’]t"
)
#: ⛔ THE NON-FINITE VERB PHRASE IS A SECOND VERB, AND THE CLASS IS A ROLE, NOT TWO SPELLINGS
#: (round-28 review, P1, measured). Listing literally `to be|being` closed the two spellings the
#: previous round measured and left the rest of the class open — every one of these still ROUTED
#: the whole ended population with the qualifier dropped and `signal=None`:
#:
#:   `is the export getting archived`            <- colloquial progressive
#:   `is the export to eventually be archived`   <- ONE adverb splits `to be`
#:   `is the export to get archived` / `… to become archived`
#:
#: One home, two consumers — `_COPULA_INFINITIVE_REFUSAL` (the `is|are|was|were` branch's own
#: refusal lookahead; it is NOT in the shared `_COPULA_SUBJECT`, which the perfect branch also
#: uses — see that constant) and `_COPULA_LONG_SUBJECT`'s arm B — because fixing the refusal
#: without widening the report trades a wrong route for a silent drop: measured, with only the
#: refusal side widened, `is the export getting archived` came back `signal=None`.
#: ⛔ AND THE FOUR PHRASAL / RESULTATIVE VERBS THE FIRST CUT LEFT OUT (fresh review, P2,
#: measured): `to wind up`, `to end up`, `to turn out` and `to stay` are the SAME scheduled or
#: continuing frame — each still ROUTED the whole ended population with the qualifier dropped and
#: `signal=None`, `is the export to wind up archived` among them, which is #14033's own defect
#: four spellings over. Measured on the widened class over 18 998 probes: 1 040 move
#: ROUTE -> REFUSED+REPORTED (`is_copula_long_subject`) and 140 move SILENT -> REFUSED+REPORTED,
#: with **0** route -> silence moves — the one direction this widening must not take, because
#: refusing without reporting is the other half of #14033, and it is why both consumers move
#: together (a refusal-side-only widening was measured doing exactly that in the previous round).
#: The PREPOSITIONAL `to` is untouched: `is the export to Europe archived` still routes, pinned.
#: ⛔ THE `-ing` ALTERNATIVES ARE THE SAME FRAME, ONE SPELLING OVER (round-10 review, P1,
#: measured). The class shipped with `being|getting` only, so `is the export becoming archived`
#: ROUTED the whole ended population with the caller's qualifier dropped and `signal=None` —
#: fail-open, the direction this issue exists to close — while `is the export being archived`
#: refused correctly. Every `-ing` alternative now mirrors its `to <verb>` twin exactly, so a
#: widening of one cannot leave the other behind: `becoming`, `staying`, `remaining`, `keeping`,
#: `ending up`, `winding up`, `turning out`. `to remain`/`to keep` move in with them for the same
#: reason (they were named a residual and are the same progressive frame).
#: ⛔ WHAT IS STILL NOT COVERED IS STATED, NOT IMPLIED: the `to <word> <word> <verb>` window is
#: bounded to TWO preceding words (a third puts the phrase outside the subject's own word bound
#: anyway), and a non-finite verb outside this list still routes — a closed list is the mechanism
#: this module uses everywhere and the residual it always carries.
_NONFINITE_COPULA = (
    r"(?:to\s+(?:[a-z]+\s+){0,2}"
    r"(?:be|get|become|became|wind up|end up|turn out|stay|remain|keep)"
    r"|being|getting|becoming|staying|remaining|keeping"
    r"|ending\s+up|winding\s+up|turning\s+out)"
)
#: ⛔ THE FRAME'S ADVERB GAP IS THE MODULE'S OWN ADVERB CLASS, NOT A FRESH HAND-TYPED LIST
#: (round-12 review, P1, measured). The frame-aware guard spelled its gap as
#: `[a-z]+ly|already|ever|really|still|actually|just|only|never|not` — a hand-typed list, not
#: the adverb class :data:`_EXISTENCE_ADVERB` already consolidated across four review rounds
#: for exactly this failure ("THE `-ly` CLASS IS NOT THE ADVERB CLASS"). Any adverbial outside
#: it re-opened the fail-open the guard exists to close: measured at `faa8b3fef96c` and again
#: at `9dedcf60e7` (whose own fix bounded the COUNT to four and left the CLASS untouched),
#: each of these ROUTED the whole ended population with the caller's qualifier dropped and
#: `signal=None`, while the parent refused them — `is the export being very archived`,
#: `is the export to be very archived`, `is the export being almost archived`,
#: `is the export being no longer archived`, `is the export being often archived`,
#: `is the export becoming soon archived`. Two axes, two fixes: the bound is the consumer's
#: (four, as `_COPULA_PREFIX` absorbs), and the CLASS is the module's shared one.
#: ⛔ AND THE COMMA IS PART OF THE GAP (round-12 review, P2, measured): `is the export being,
#: in fact, archived` was refused with NO report at BOTH head and parent, because `\s+` between
#: the members does not match `, `. A cue that reaches no answer must REPORT, so the separator
#: is `[,\s]+` — the idiom :data:`_EXISTENCE_HELPER` already uses for the same reason.
#: ⛔ ONE SLOT FOR BOTH CONSUMERS, because a report arm narrower than the refusal arm converts
#: a fail-open into a SILENCE (measured: widening only the refusal answers
#: `is the export being very archived` with `(None, None, None)`).
_COPULA_FRAME_ADVERB_SLOT = rf"[,\s]+(?:(?:{_EXISTENCE_ADVERB})\b[,\s]+){{0,4}}"

_EXISTENCE_GUARD = (
    r"(?![^?]{0,60}?\b(?:because|since|when|while|after|before|now|if|unless|given|"
    r"as|where|so|but|although|though|until|whether|once|whereas|why|how)(?![-/])\b)"
    r"(?![^?]{0,60}?\b[a-z0-9][a-z0-9'’._/-]*\s+(?:that|which|who|whom|whose)\b)"
    r"(?![^?]{0,60}?\b(?:(?:the|a|an|its|his|her|their|our|your|my|these|those|this|"
    # ⛔ THE DETERMINER IS OPTIONAL AND THE SUBJECT MAY BE A BARE NOUN (round-16 review,
    # P1, measured). Requiring `<det> <noun>` left the same asyndetic hijack open for
    # every subject spelled without an article — `have any users reported v3 has been
    # deprecated`, `has anyone complained billing has been deprecated`,
    # `have any users reported the search index has been deprecated` all ROUTED and
    # dropped the caller's predicate (`reported`, `complained`), while the pinned twin
    # `have any users reported the vendor has been deprecated` was refused: the two
    # differ only by `the`. The PRONOUNS are excluded instead of the bare noun, because
    # a pronoun subject is the one class that is legitimately the RELATIVE clause's
    # (`has anything we have been tracking been withdrawn` routes; see the fourth
    # lookahead below, which owns that boundary).
    r"that)\s+)?(?!it\b|they\b|he\b|she\b|we\b|you\b|i\b)"
    r"[a-z0-9][a-z0-9'’._/-]*\s+"
    # ⛔ A CONTRACTION IS NOT A `not` TOKEN (round-26 review, P1, measured): the negation
    # slot accepts `not` anywhere in the gap, but `hasn't` carries its negation INSIDE
    # the auxiliary, so an anchor spelled `has` followed by space/punctuation never
    # matched it, and all 17 contracted twins — `have any users reported the vendor
    # ⛔ 17, NOT 18 (review P3, measured): the alternation below enumerates 17, and the
    # test's `_CONTRACTED_AUXILIARIES` — whose docstring says "one per member" — is 17
    # too. The "18" this sentence carried before was a count nobody held, which is the
    # same defect class as a pin that does not pin.
    # hasn't been deprecated` — routed the whole ended population with the caller's
    # predicate dropped and `signal=None`, while every UN-contracted twin was refused.
    # This is a CLOSED set, unlike the open adverbial class: one alternation closes it.
    rf"(?:{_CONTRACTED_AUX}|"
    # ⛔ AND `do|does|did` ARE NOT HERE (round-26 review, P2, measured): none of them can
    # precede `been`, so they add NO detection power to a guard whose window requires
    # `been` — their only effect was to let an ordinary verb fill the noun slot and the
    # next verb fill the auxiliary: `has anything we said we would do been cancelled`
    # matched `noun='would' aux='do'` and was wrongly refused. Measured: 6 of 6
    # legitimate capstone questions refused with them, all 6 route without, 0 pins flip.
    # ⛔ AND THE CLITIC SPELLINGS (round-27 review, P2, measured): `'s`, `'ve` and `'d`
    # are the COMMONEST English spelling of exactly these auxiliaries — `have any users
    # reported the vendor's been deprecated`, `have any customers said they've been
    # cancelled` — and all 5 of 5 constructed rows routed with the caller's predicate
    # dropped and `signal=None`. The clitic attaches to the SUBJECT, so the noun slot
    # takes the noun and the auxiliary alternation takes `<noun>'s`, which is why this is
    # one pattern and not a new lookahead. The apostrophe inventory is normalised above,
    # so `’` is already `'` by the time any of this runs.
    r"[a-z0-9][a-z0-9'’._/-]*['’](?:s|ve|d)|"
    r"has|have|had|is|are|was|were|will|would|can|could|may|might|"
    # ⛔ AND THE CLAUSE'S OWN PARTICIPLE ANCHORS IT (round-24 review, P2, measured):
    # without the `been <ENDING>` anchor the widened adverb slot refused a RELATIVE
    # clause whose own verb is `<aux> <adverb> been <other participle>`, which is the
    # window content the module allows on purpose — `has anything the board has recently
    # been briefed on been withdrawn` ROUTED before the adverb slot and went silent
    # after. The anchor is the same one the pronoun branch below already uses: the
    # lookahead fires only when the second clause's passive ends an ENDING participle.
    rf"must|should)[,\s]+{_EXISTENCE_HELPER}\s+{_EXISTENCE_REFUSAL_ADVERB_SLOT}(?:{_ENDING_WORDS})\b(?!\s*been\b))"
    # ⛔ AND A PRONOUN SUBJECT IS ITS OWN ALTERNATIVE (round-14 review, P2, measured):
    # the slot above requires `<determiner> <noun>`, which a pronoun has NEITHER of, so
    # `have any users reported it has been deprecated` was ROUTED to the whole ended
    # population and the caller's own predicate (`reported`) was dropped — the exact
    # hijack this guard exists to refuse, one subject class wide.
    # ⛔ BUT THE PRONOUN IS THE SECOND CLAUSE'S SUBJECT, NOT THE QUESTION'S (#14033
    # round-15 review, P2, measured — the first cut of THIS lookahead caused it): a
    # RELATIVE clause's subject can be a pronoun too, and there the participle that
    # follows `been` belongs to the RELATIVE clause, which is the one place inside the
    # window the module allows (`has anything we have been tracking been withdrawn` is
    # the capstone question in the present perfect, and it ROUTES). So the lookahead
    # fires only when the pronoun clause's own `been` introduces an ENDING participle —
    # that is what separates `it has been deprecated` (asyndetic: the ending is the
    # relative clause's) from `we have been tracking` (relative: the ending is still the
    # question's).
    rf"(?![^?]{{0,60}}?\b(?:it|they|he|she|we|you)\s+"
    # ⛔ A CONTRACTION IS NOT A `not` TOKEN (round-26 review, P1, measured): the negation
    # slot accepts `not` anywhere in the gap, but `hasn't` carries its negation INSIDE
    # the auxiliary, so an anchor spelled `has` followed by space/punctuation never
    # matched it, and all 17 contracted twins — `have any users reported the vendor
    # hasn't been deprecated` — routed the whole ended population with the caller's
    # predicate dropped and `signal=None`, while every UN-contracted twin was refused.
    # This is a CLOSED set, unlike the open adverbial class: one alternation closes it.
    rf"(?:{_CONTRACTED_AUX}|"
    # ⛔ AND `do|does|did` ARE NOT HERE (round-26 review, P2, measured): none of them can
    # precede `been`, so they add NO detection power to a guard whose window requires
    # `been` — their only effect was to let an ordinary verb fill the noun slot and the
    # next verb fill the auxiliary: `has anything we said we would do been cancelled`
    # matched `noun='would' aux='do'` and was wrongly refused. Measured: 6 of 6
    # legitimate capstone questions refused with them, all 6 route without, 0 pins flip.
    # ⛔ AND THE CLITIC SPELLINGS (round-27 review, P2, measured): `'s`, `'ve` and `'d`
    # are the COMMONEST English spelling of exactly these auxiliaries — `have any users
    # reported the vendor's been deprecated`, `have any customers said they've been
    # cancelled` — and all 5 of 5 constructed rows routed with the caller's predicate
    # dropped and `signal=None`. The clitic attaches to the SUBJECT, so the noun slot
    # takes the noun and the auxiliary alternation takes `<noun>'s`, which is why this is
    # one pattern and not a new lookahead. The apostrophe inventory is normalised above,
    # so `’` is already `'` by the time any of this runs.
    r"[a-z0-9][a-z0-9'’._/-]*['’](?:s|ve|d)|"
    r"has|have|had|is|are|was|were|will|would|can|could|may|might|"
    rf"must|should)[,\s]+{_EXISTENCE_HELPER}\s+{_EXISTENCE_REFUSAL_ADVERB_SLOT}(?:{_ENDING_WORDS})\b(?!\s*been\b))"
)

_EXISTENCE_WINDOW = (
    rf"[^?]{{0,60}}?\bbeen\s+{_EXISTENCE_ADVERB_SLOT}(?:{_ENDING_WORDS})\b"
)
_EXISTENCE_PREFIX = _EXISTENCE_HEAD + _EXISTENCE_GUARD + _EXISTENCE_WINDOW

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
    # the wh-phrase's HEAD noun. ⛔ NO DIGIT WIDENING HERE: `[a-z0-9]…` was tried and
    # removed (round-8 review, P2) because it buys no MEASURED string — a digit-bearing
    # word before an auxiliary is a MODIFIER (`which 2024 reports were archived`,
    # `which v3 features are deprecated`), which this shape still refuses and
    # `which_modified_noun` below reports. A widening whose only justification is a
    # comment is a rule nothing holds.
    # ⛔ AND THE LIMIT IS A SILENCE, NOT A REPORT (round-12 review, P2, measured): a
    # SINGLE-Token digit-bearing head noun is neither routed nor reported —
    # `which v3 was deprecated` and `which soc2 was archived` both come back with no
    # population and no signal, while `which v3 feature was deprecated` reports
    # (`which_modified_noun` needs TWO head words). So the modifier sentence above
    # holds for the multi-word form only; the one-word form is recall given up
    # SILENTLY, which is a gap of the same class #14033 closes and is recorded here
    # rather than promised as coverage.
    rf"[a-z][a-z'’._/-]*\s+"
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
#: ⛔ AND THE PERFECT BRANCH REQUIRES `been` IMMEDIATELY BEFORE THE PARTICIPLE,
#: which is the same attributive discriminator `existence` uses: `have any
#: customers cancelled this month` is a question about what customers did this
#: month, not about which records ended, and without this it would have been
#: reported as a missed state cue. ⛔ `already`/`ever` are admitted ONLY on the
#: copula branch (`is|are|was|were … already <participle>`); in the perfect branch
#: they read as an ACTIVE third-party action, so they are refused there — see the
#: round-7 hijack note below `_COPULA_SUBJECT`, which is the one statement of that
#: rule and which this paragraph used to contradict.
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
#: optional determiner plus ONE to FOUR contiguous lowercase words.
#: ⛔ THE BOUND IS NOT SUFFICIENT ON ITS OWN, and the justification that said it was
#: is the round-14 review's P3. It read "a clause needs a SUBORDINATOR (`because`,
#: `that`, …) and a second finite verb, and neither fits inside a four-word noun
#: phrase" — measured FALSE: `is the plan that the board archived` carries a
#: subordinator inside the four-word bound and ROUTED, because `archived` reads as the
#: relative clause's verb and the question has no predicate of its own. So the bound is
#: paired with an explicit subordinator/relativizer lookahead INSIDE it, and then the
#: participle is the question's own predicate again, which is the whole claim. The
#: recall given up is stated rather than discovered: a subject with a modifier
#: clause inside it (`is the onboarding flow, as agreed in March, retired`) keeps the
#: relevance read, and so does a subject longer than four words. On the
#: `is|are|was|were` branch the latter REPORTS itself through `_COPULA_LONG_SUBJECT`
#: below rather than going silent; on the `has|have|had … been` branch it is still
#: SILENT, which is the limit the notice-scope paragraph above already states.
#: ⛔ A SUBJECT WORD MAY CARRY DIGITS (`v3`, `SOC2`, `2024`, `Q4`), and that is a
#: measured fix rather than a detail: the first subject class was `[a-z]…`, which
#: cannot spell `v3`, so `is the v3 export deprecated?` was BOTH unrouted and
#: unreported. The input is already lower-cased, so the old class bought
#: case-insensitivity and nothing else.
_COPULA_SUBJECT = (
    # …the lookahead is the round-14 P3 guard: a SUBORDINATOR anywhere inside the
    # bounded subject means the ending participle belongs to somebody else's clause,
    # so the shape must not fire on it (`is the plan that the board archived`).
    # ⛔ IT REQUIRES A PRECEDING WORD (`{1,5}`, not `{0,5}`), and that is a measured
    # correction: `that` is ALSO the subject's own DETERMINER, so the first cut fired
    # on `is that endpoint deprecated` and refused a legitimate state question. A
    # subordinator cannot open a noun phrase, so a word before it is the
    # discriminator — the same rule the `existence` relativizer guard states.
    # ⛔ AND THE LIST OMITTED THE FREE-RELATIVE PRONOUNS, THEN A WHOLE REDUCED-RELATIVE
    # CLASS (round-17 review, P1, measured — the PR's OWN regression, absent on main).
    # `what` was not a member and no member is needed for the REDUCED relative, so a
    # subject that contains either routed:
    #
    #   `is that what the board archived`      <- free relative (`what`)
    #   `is it what we archived`               <- free relative, no subordinator at all
    #   `is that the endpoint we deprecated`   <- reduced relative (NP + clause)
    #   `is the report the board archived`     <- reduced relative, the participle is
    #                                             the relative clause's verb
    #
    # Every one was answered with the WHOLE ended population with the caller's own
    # predicate dropped, and `signal` stayed None — so it was not even REPORTED. That
    # is the #14033 defect itself, in the direction this module rates worst.
    # ⛔ TWO FIXES, each anchored on a grammatical role rather than a vocabulary:
    # (a) `what|how|why` join the list — a free relative's pronoun CAN follow a word
    #     (`that what`, `it what`), so the existing preceding-word rule keeps
    #     `is what archived`-shaped subjects alone;
    # (b) a DETERMINER AFTER AT LEAST ONE WORD is a SECOND NOUN PHRASE, which is what a
    #     reduced relative needs (`report the board`, `endpoint we`): a noun phrase
    #     cannot contain a second determiner, so the same preceding-word discriminator
    #     applies — the subject's OWN determiner opens the span and is untouched, which
    #     is what keeps `is that endpoint deprecated` and `is the v3 export deprecated`
    #     routing. The recall this gives up — including the part of it that goes SILENT
    #     — is stated once, at the end of this block, so a later reader cannot take the
    #     rule from one sentence and the recall from another.
    # ⛔ TWO OVER-REFUSALS THE FIRST CUT OF THAT GUARD CAUSED (round-18 review, P2, both
    # measured as REGRESSIONS — these routed before the guard and went SILENT after):
    # (1) DETERMINER STACKING is not a second noun phrase. `all|both|half|such` are
    #     PRE-determiners, so `all the`, `both the`, `half the`, `such a` is ONE noun
    #     phrase, and `is all the data archived`, `is all our old plans retired`,
    #     `is half the exports archived`, `is such a report archived`, `is both the
    #     reports archived` and the perfect branch `has all the data been archived` all
    #     went silent. So is an `of`-PARTITIVE (`is one of the exports archived`,
    #     `has none of the data been archived`) — see the position rule below, which is
    #     the one that handles both.
    # (2) A HYPHEN OR SLASH IS NOT A WORD BOUNDARY FOR THIS RULE. `\b` matches before
    #     `-` and `/`, so `how-to`, `what-if`, `all-hands` and `a/b` read as a
    #     subordinator/determiner followed by a word, and `is the how-to guide archived`,
    #     `is the what-if scenario archived`, `is our all-hands meeting cancelled` and
    #     `is the a/b test cancelled` went silent. The token therefore has to END its
    #     word. (This also repairs the pre-existing twins on the older tokens:
    #     `is the when-in-doubt policy archived` routes again.)
    # ⛔ AND THE RECALL THAT IS STILL GIVEN UP IS STATED, INCLUDING THE SILENT PART:
    # a subject whose tail is a SECOND NOUN PHRASE (`is the report the board archived`) is
    # refused. A LONG one REPORTS itself (`is our v3 export the one archived` ->
    # `is_copula_long_subject`) because it exceeds the four-word subject bound; a SHORT
    # one is SILENT (`is the report the board archived` -> `signal is None`), which is the
    # module's stated notice-scope limit rather than a surprise. Both are measured, and
    # the second is the price of closing the route: the same expression that refuses a
    # reduced relative is what a caller sees as the unchanged relevance read.
    # ⛔ THE PRE-DETERMINER EXCLUSION IS IN THE POSITION THAT MATTERS, NOT IN EVERY TICK
    # OF THE WORD RUN (round-19 review, P1 + P2, both measured) — and the first cut of it
    # got that wrong twice over. It read
    #
    #     (?: (?!(?:all|both|half|such…)) <word> \s+ ){1,5} <determiner>
    #
    # which excludes a pre-determiner from EVERY word position, so a subject that OPENS
    # with one cannot start the run at all and the whole guard is SKIPPED — not narrowed.
    # `is every report the board archived` and `is all the data the board archived` are
    # the same reduced-relative shape as `is the report the board archived` and were
    # answered with the WHOLE ended population, `signal=None`: round-17's P1 class,
    # re-opened by its own fix. And the same over-broad exclusion is what kept the
    # `of`-PARTITIVE silent: `is one of the exports archived` and `has none of the data
    # been archived` are ONE noun phrase, routed before round 17, and were refused with
    # no notice.
    # So the rule is stated as what it is — a determiner is a SECOND noun phrase only if
    # the word DIRECTLY before it is not a pre-determiner and not `of`:
    # `is all the data` (stacking) and `is one of the exports` (partitive) do not fire;
    # `is every report the board archived` and `is the report the board archived` do.
    # ⛔ AND THE DETERMINER LIST WAS NARROWER THAN THE PROSE ABOVE CLAIMED (round-20
    # review, P2, measured). "a subject whose tail is a SECOND NOUN PHRASE is refused"
    # was FALSE for a second NP opening with a POSSESSIVE (`his|her`) or with a
    # PRE-DETERMINER, and for a second NP with NO determiner at all — five content
    # questions still ROUTED to the whole ended population with `signal=None`:
    #
    #   `is the report his team archived`               <- possessive second NP
    #   `is the contract every employee signed archived` <- pre-determiner second NP
    #   `is the report each manager reviewed archived`  <- …or `each`
    #   `is the dashboard all customers use deprecated` <- …or `all`
    #   `is the export customers requested archived`    <- second NP with NO determiner
    #
    # The list is now COMPLETE OVER ITS OWN TABLE — not over English (round-21 review,
    # P2): `his|her` (the sibling list's spelling) and the pre-determiners are members, and
    # the boundary is named so a later reader does not read "refused" as "all of them".
    # ⛔ THE `-ed` ANCHOR THAT TRIED TO CLOSE THE DETERMINER-LESS CASE IS WITHDRAWN
    # (round-21 review, P1, measured): "an `-ed` word" is not "a finite verb" — `feed`,
    # `seed`, `need`, `bed` and `shed` all END in `ed`, so it silently refused a subject
    # whose HEAD NOUN is one of them (`is the feed archived`, `is the seed archived`,
    # `is the migration seed archived`, `is the deprecated feed archived`), where the
    # revision BEFORE round 20 (`e84612d43f`) routed. A SILENT refusal of an ordinary state
    # question is the second-worst outcome this module recognises and it bought exactly
    # one spelling, so it is gone. ⛔ THE PROSE NAMES ABSOLUTE REVISIONS, NEVER RELATIVE
    # ONES (round-23 review, P3): a relative rev is stable only in the working copy it was
    # drafted in — the first cut of this note said "HEAD~1", which by the time it was read
    # had become round 21. The rows route at `e84612d43f`; round 20 (`6a301c4647`) refuses
    # them; round 21 is `a6225cbad1`.
    # ⛔ AND THE WIDENED LIST REFUSED DEGREE WORDS INSIDE THE SUBJECT (round-21 review,
    # P1, measured): `is the most recent export archived` and `is the data all archived`
    # returned `population=None, signal=None` where `e84612d43f` routed. THREE POSITION
    # rules fix that — in the position slot, never in the word-run slot (the wrong
    # placement kills the guard entirely, the round-19 failure mode):
    #   (a) a determiner-vocabulary word DIRECTLY PRECEDED BY A DETERMINER is one NP
    #       (`the most recent`), so it cannot open a second one;
    #   (b) a determiner-vocabulary word IMMEDIATELY FOLLOWED BY THE PARTICIPLE is a
    #       POST-NOMINAL quantifier (`the data all archived`), not a second NP;
    #   (c) …nor is one followed by a DEGREE or NEGATION adverb (`no longer`, `any longer`,
    #       `all but`, `most recent`). ⛔ RULE (c) IS THE ROUND-22 REVIEW'S P2, measured:
    #       without it `is the export no longer archived`, `is our plan no longer retired`,
    #       `is the report any longer archived`, `is the invoice no longer cancelled`,
    #       `is the export all but archived` and `is the export most recent archived` were
    #       SILENT while the semantically identical `is the export not archived` ROUTED —
    #       an internally inconsistent pair, with no notice on the refused half.
    #   ⛔ AND (b) AND (c) MUST REACH THROUGH `been` (round-23 review, P2, measured): the
    #       quantifier's post-nominal position is `has the data all BEEN archived`, and with
    #       the participle immediately after the quantifier the guard fired on all 77/77
    #       `has|have|had the <noun> <quantifier> been <ending>` probes — SILENT — while
    #       both flat twins ROUTED (`has all the data been archived`, `has the data been
    #       archived`) and are pinned as routing. So the lookahead accepts `been` + the
    #       participle too, which is the same position one auxiliary later.
    # ⛔ AND THE RESIDUAL THIS GUARD CANNOT CLOSE IS STATED RATHER THAN CALLED CLOSED:
    # a second NP with NO determiner (`is the export customers requested archived`) and one
    # opening with an indefinite quantifier this module does not name (`several`, `fewer`,
    # `other`, `another`, `much`, `enough` — `is the export several teams use deprecated`)
    # still ROUTE, because telling a bare noun from a bare verb needs a lexicon this
    # detector does not have.
    # ⛔ …AND ONE MORE, ADDED WHEN THE PREPOSITION FIX BELOW WAS MEASURED: a qualifier
    # introduced by a SUBORDINATOR is still SILENT on this shape — `is the export archived
    # because of the migration`, `is the onboarding flow retired since the merger`. That is
    # not the same defect as the preposition case this round fixed: a `because`/`since`
    # clause turns the question into a CAUSAL or TEMPORAL one, which this module refuses
    # deliberately (`is the export broken because the schema was deprecated` is the pinned
    # content class), and a boundary-naming `since` is the class `wh_whole_question` refuses
    # by design. What is honestly still owed is the NOTICE — `_BLOCKED_TAIL` lists both
    # words, so the closed-list contract promises a report the shape does not deliver. The
    # guard cannot see WHERE its subordinator sits without bounding every lookahead at the
    # participle, and that bound was measured to re-ROUTE the round-16 hijack
    # (`has anyone complained billing has been deprecated`), so it is recorded here as a
    # KNOWN silence rather than traded for a routing regression.
    r"(?!(?:[a-z0-9][a-z0-9'’._/-]*\s+){1,5}"
    r"(?:that|which|who|whom|whose|whether|because|since|when|while|if|unless|given|"
    # ⛔ AND A SECOND AUXILIARY IS A NESTED CLAUSE (round-16 review, measured):
    # `has anyone complained billing has been deprecated` put the caller's own verb
    # (`complained`) plus a whole asyndetic clause (`billing has been …`) inside the
    # perfect branch's subject and ROUTED to the whole ended population with the
    # predicate dropped. The copula the shape needs is the one AFTER the subject, so an
    # auxiliary inside the subject refuses the shape. Measured after the round-20/21/22
    # guards: this row still routed, and every routing row in the suite still routes with
    # the tokens below (`is the data all archived`, `has the onboarding flow been
    # retired`, `is the v3 export deprecated`).
    # ⛔ AND A CONTRACTED AUXILIARY BELONGS TO THE SAME CLASS (round-28 review, P1,
    # measured). This lookahead is the THIRD copy of the second-verb class and the one
    # that had no contractions at all, so the perfect branch below swallowed an asyndetic
    # second clause whose own auxiliary was contracted: the contracted twins ROUTED the
    # whole ended population with the caller's predicate dropped and `signal=None`, while
    # every UN-contracted twin was refused. The class is interpolated from
    # :data:`_CONTRACTED_AUX` — one home, THREE consumers (this lookahead and both
    # `_EXISTENCE_GUARD` lookaheads), which is what keeps THOSE three from drifting.
    # ⛔ …AND THAT IS THE WHOLE CLAIM. Two more copies of the finite-auxiliary class live
    # in this module and neither is a `_CONTRACTED_AUX` consumer, on purpose and measured
    # (fresh review, P2 — the prose here used to promise "so the fourth copy cannot go
    # missing", which was false on the day it was written):
    #   * `_WHICH_MODIFIED_NOUN`'s auxiliary slot was a fourth copy and IS interpolated
    #     now, because its drift was a SILENCE — `which customer claims hasn't been
    #     archived` returned `population=None, signal=None` while the un-contracted twin
    #     reported `which_modified_noun`;
    #   * `_COPULA_LONG_SUBJECT`'s arm A keeps its OWN hand-typed run and adds NO
    #     contraction, because there the contraction is a NEGATIVE member (a second finite
    #     verb in the subject SUPPRESSES the report), so interpolating it would trade 1 180
    #     measured notices for silence — the direction this module refuses. Pinned by
    #     `test_the_contraction_in_a_LONG_subject_stays_on_the_safe_side`.
    rf"where|whereas|after|before|now|what|how|why|{_CONTRACTED_AUX}|"
    r"has|have|had|is|are|was|were|will|would|can|could|may|might|must|should|do|does|did)(?![-/])\b)"
    # ⛔ THE NON-FINITE COPULA IS **NOT** IN THIS LIST, DELIBERATELY (review P1,
    # measured): it belongs to the `is|are|was|were` branch ONLY, via
    # `{_COPULA_INFINITIVE_REFUSAL}` below. This guard is SHARED with the perfect branch,
    # where the question's predicate is already anchored by `has … been`, so a
    # `to be <participle>` inside the subject can only be a POST-NOMINAL MODIFIER and the
    # question is legitimate. Excluding it here refused those questions SILENTLY
    # (`missed_route_payload` is None — `_COPULA_LONG_SUBJECT` is anchored
    # `^(?:is|are|was|were)`). Measured on the head that had it here:
    #   `has the contract to be signed been archived`  -> None / None   (was `ended`)
    #   `has the report to be reviewed been archived`  -> None / None   (was `ended`)
    #   `has the decision to be taken been archived`   -> None / None   (was `ended`)
    #   `has the report being reviewed been archived`  -> None / None   (was `ended`)
    # i.e. a routed->silent regression against this PR's own prior head, on ordinary
    # English noun phrases — the exact defect class this PR exists to remove.
    r"(?!(?:[a-z0-9][a-z0-9'’._/-]*\s+){0,4}"
    # ⛔ A PREPOSITION IS NOT A SECOND NOUN PHRASE (review P1, measured). `of` was
    # already excluded here — a genitive (`the export of the vendor`) is ONE noun
    # phrase — but no OTHER preposition was, so a `<prep> the <noun>` QUALIFIER
    # sitting after the participle was read as a second NP inside the subject and
    # REFUSED the shape. The result was neither a route nor a notice: `is the export
    # archived by the vendor`, `is the Q3 export archived in the old sharepoint` and
    # `is the billing service deprecated for the new plans` all came back
    # `signal=None` — SILENT — which is #14033's own defect (an unrouted state
    # question that is indistinguishable from "nothing has ended") reproduced on the
    # shape this PR ADDS. The pinned row that should have caught it pins only
    # `… retired by legal`, the `<prep> <bare noun>` spelling — the one shape the
    # guard happened to let through. So the PREPOSITION CLASS is excluded here, and
    # it is not typed here: it is interpolated from :data:`_PREPOSITIONS`, which is
    # what makes a reported qualifier of that form reportable instead of swallowed by
    # a guard looking past the participle.
    # ⛔ ONE HOME, TWO CONSUMERS (round-25, measured — the third round on this class).
    # This list and `_BLOCKED_TAIL` each carried their OWN hand-typed copy of the class,
    # so each round fixed the copy the reviewer happened to probe and left the other
    # short: round-1 P1 (`by the vendor`) and round-2 P2 (`during`/`until`/`as`/`than`)
    # were both drift between the two, and the prose here claimed the class closed
    # while `despite`, `without`, `within`, `under`, `through`, `across`, `between`,
    # `via`, `near`, `beyond`, `upon`, `around`, `above`, `below` … all still came back
    # `signal=None` — measured, on the unchanged head. Both sites now interpolate
    # `_PREPOSITIONS`, and the membership is pinned by driving every member through the
    # detector, so a fourth drift reddens instead of shipping.
    # ⛔ WHAT IS STILL NOT COVERED IS STATED, NOT IMPLIED — AND IT HAS TWO CAUSES, NOT
    # ONE (review at `5b82bcdf03`, P2, measured: the previous draft of this sentence
    # gave BOTH groups the subordinator reason, which is false for the second):
    #   (a) SUBORDINATOR members (`because`, `when`, `if`, and the three that are both
    #       preposition and subordinator — `after`, `before`, `since`) are refused by the
    #       SUBORDINATOR lookahead above, which refuses inside the subject and cannot
    #       tell a tail from a subject without bounding itself at the participle —
    #       measured to re-route the round-16 hijack. Those three are the notice
    #       genuinely still owed, which is why they stand OUTSIDE `_PREPOSITIONS` rather
    #       than being quietly counted as covered by it. Excluding them HERE is a
    #       NO-OP: measured, adding all three flips 0 of 277 probe outcomes.
    #   (b) CONJUNCTIONS (`and|or|but|so|yet`) are NOT in that lookahead at all. What
    #       refuses them is the "second noun phrase" test BELOW reading a coordination
    #       as one — a LIST decision, not that guard's participle bound: excluding them
    #       here DOES report them (measured: 5 probes flip, 0 pinned rows change). That
    #       is a recall widening with its own review, so it is stated rather than made,
    #       and `test_the_conjunction_silence_is_a_LIST_decision_not_a_participle_bound`
    #       pins it.
    # ⛔ AND A WORD LIST CANNOT SETTLE (a) EITHER: `after`/`before`/`since` are `ADP`
    # before a nominal (`since the merger`) and `SCONJ` before a clause (`since the
    # merger closed`), so the tags separate them only after the fact.
    r"(?!(?:all|both|half|such|many|most|some|any|no|each|every|"
    rf"{_QUALIFIER_PREPOSITION_RE}|"
    r"the|a|an|our|my|its|their|these|those|this|that|your|his|her)\b)"
    r"[a-z0-9][a-z0-9'’._/-]*\s+"
    r"(?:the|a|an|our|my|its|their|these|those|this|that|your|his|her|"
    rf"all|both|half|such|many|most|some|any|no|each|every)(?![-/])\b"
    rf"(?!\s+(?:(?:longer|more|less|recent|but|yet|than)\b|"
    rf"(?:been\s+)?(?:{_ENDING_WORDS})\b)))"
    # ⛔ AND A SUBJECT PRONOUN MAY ONLY **BE** THE SUBJECT (round-18 review, P1,
    # measured — the SAME #14033 defect still open after round 17's two guards, in the
    # same worst direction). Both guards above miss it: a REDUCED RELATIVE whose
    # subject is a pronoun needs no second determiner, and an EXTRAPOSED frame
    # (`is it <adjective> <subject> <participle>`) has no determiner at all:
    #
    #   `is the plan we archived`      <- reduced relative, pronoun subject (`we`)
    #   `is the report she archived`   <- …or `she`/`they`/`you`
    #   `is it clear legal retracted`  <- extraposed `it`, the REAL subject follows it
    #   `is there evidence legal retracted`
    #
    # All fourteen probes were answered with the WHOLE ended population with the
    # caller's own predicate dropped, and `signal` stayed None — so it was not even
    # reported. `is_copula` is NEW in this PR, so on `main` every one of them is an
    # ordinary relevance read: this class is the PR's own regression.
    # ⛔ THE FIX IS A ROLE, NOT A WORD LIST: a personal pronoun can only BE a subject,
    # never sit INSIDE a longer noun phrase — the pronoun is what a second clause opens
    # with (`the plan we …`) or, when it leads, the SUBJECT IS the pronoun and every
    # word after it belongs to another slot (an adverb, which the prefix owns, or the
    # participle). So the subject is now TWO disjoint branches — a noun phrase, which
    # may not contain a pronoun at all, or a BARE PRONOUN — and nothing else:
    #   `is it archived` / `is it really retired` still ROUTE (the bare-pronoun branch,
    #   with the adverb in the prefix's own adverb slot);
    #   `is the plan we archived` / `is it clear legal retracted` are REFUSED.
    # The recall given up is stated rather than discovered: a pronoun subject followed by
    # a word that is NOT one of the prefix's adverbs (`is it probably retired`) keeps the
    # relevance read, and so does a subject whose noun phrase contains a pronoun.
    r"(?:"
    r"(?!(?:[a-z0-9][a-z0-9'’._/-]*\s+){0,5}"
    r"(?:i|you|he|she|it|we|they|there)(?![-/])\b)"
    # ⛔ A SUBJECT WORD MAY NOT CARRY AN AUXILIARY CLITIC (round-28 review, P1, measured):
    # the word class below admits `'`, so it swallowed `billing's` whole and the copula
    # shape matched the FULL string on the first pass — `have any users reported billing's
    # been deprecated` returned the ended population with the caller's predicate dropped
    # and `signal=None`, while its DETERMINER twin (`the billing's been deprecated`) was
    # refused with the notice. One article apart, opposite verdicts: the guard in the
    # branch above could not help, because this shape is matched BEFORE it and never
    # consults it. Excluding the clitic here lets the row fall through to
    # `_ENDING_SIGNALS`, where the clitic alternative refuses it and reports.
    r"(?:(?:the|our|my|its|their|these|those|this|that|all|your)\s+)?"
    # ⛔ ONLY THE AUXILIARY READING, NOT EVERY POSSESSIVE (round-29 review, P1, measured):
    # the first cut of this exclusion was `(?![a-z0-9'’._/-]*['’](?:s|ve|d)\b)` at EVERY
    # subject word, which refused the whole POSSESSIVE-SUBJECT class — `is the board's
    # decision archived`, `is Q3's report archived`, `is O'Brien's claim retracted` — 800
    # of 800 rows in a 4-head × 2-det × 20-subject × 5-participle population, SILENTLY
    # (`signal=None`, so not even a notice). Those are exactly the multi-word subjects this
    # slot was widened to admit, and `21cc92e90c` served them (⛔ ABSOLUTE, never `HEAD~1` —
    # the file forbids relative revisions in prose because the first cut of a note twenty
    # lines above said `HEAD~1` and by the time it was read it had become round 21; measured
    # here, `a3886ee055^` is `3d4d5d5b62`, which REFUSED all three of these silently — it is
    # the very regression this sentence describes, not the revision that served them).
    # The clitic that matters is the
    # one on the FINAL word that IS the auxiliary (`billing's been`), so the exclusion is
    # anchored on the following `been`.
    # ⚠️ A "guard only the last word" rewrite (`(?:word\s+){0,3}(?!clitic)word`) LEAKS: the
    # regex backtracks the clitic word into the leading group and `is billing's been
    # deprecated` routes again (48 of 96 measured).
    # ⛔ AND THE ANCHOR CANNOT BE ADJACENT (round-30 review, P1, measured): `\s+been(?![-/])\b`
    # was defeated by ONE ADVERB — `has billing's just been deprecated`,
    # `has the vendor's recently been deprecated`, `is the board's very recently been
    # deprecated` all served the ended population with the caller's predicate dropped and
    # `signal=None`, because the interruption let the row into this slot, whose word class
    # accepts the literal `been`. Over a 37,800-row clitic population, 17,568 rows newly
    # routed and 1,296 that had been refused WITH a notice (`is_copula_long_subject`)
    # became silent. So the gap between the clitic and `been` tolerates up to three
    # adverbial words, and `been` itself is FORBIDDEN as a subject word — that is what
    # stops the slot from swallowing the whole clause.
    # ⛔ AND THE GAP IS THE MODULE'S OWN ADVERB CLASS, NOT "ANY WORD" (round-30 review,
    # measured both ways): a free `[a-z-]+` gap closed the hijack and then refused the
    # POSSESSIVE-SUBJECT class the same way — `has the client's contract been archived` and
    # `has the vendor's invoice been retired` are legitimate questions whose subject noun
    # happens to sit between the clitic and `been`, and they went silent. The distinguishing
    # feature is grammatical ROLE (adverb vs noun), which needs the lexicon this detector
    # does not have, so the gap is `_EXISTENCE_ADVERB` — the same closed table the asyndetic
    # guard already uses. RESIDUAL: an adverb outside that table re-opens the hijack for
    # this one spelling; it is the same boundary stated at `_EXISTENCE_ADVERB` itself.
    # ⛔ AND `been\b` IS NOT ENOUGH ON ITS OWN (round-10 review, P3, measured): `\b` matches
    # BEFORE a hyphen, so a POSSESSIVE followed by a `been-…` compound — `is the vendor's
    # been-signed contract archived` — was refused with NO notice while its plain twin routed.
    # The module's own idiom for a token that must END its word is `(?![-/])\b`, which
    # `_EXISTENCE_GUARD` already uses for exactly this reason; it is applied to every
    # `been` site below. Its hyphenated twin is pinned as a ROUTE in `SELECTED`.
    rf"(?![a-z0-9'’._/-]*['’](?:s|ve|d)\b(?:[,\s]+(?:{_EXISTENCE_ADVERB})){{0,3}}[,\s]+been(?![-/])\b)"
    r"(?!been(?![-/])\b)[a-z0-9][a-z0-9'’._/-]*"
    rf"(?:\s+(?![a-z0-9'’._/-]*['’](?:s|ve|d)\b(?:[,\s]+(?:{_EXISTENCE_ADVERB})){{0,3}}[,\s]+been(?![-/])\b)"
    r"(?!been(?![-/])\b)[a-z0-9][a-z0-9'’._/-]*){0,3}"
    r"|(?:i|you|he|she|it|we|they|there)(?![-/])"
    r")"
)
#: ⛔ THE PERFECT BRANCH REQUIRES `been`, AND THAT IS A HIJACK FIX (round-7 review,
#: P1). `already`/`ever` in place of `been` reads as an ACTIVE third-party action —
#: `has the partner already cancelled` asks what the partner did, not whether the
#: record ended — and the earlier cut routed it (and `has legal already retracted`),
#: serving the whole ended population with no notice. Only `is|are|was|were` can be
#: followed by a bare participle, because there the passive/adjectival reading is
#: forced.
#: ⛔ THE ADVERB SLOT CARRIES THE TIME/DEGREE ADVERBS A CALLER MAY PUT BETWEEN THE
#: SUBJECT AND ITS PARTICIPLE, and it is a CLOSED list for the same reason every other
#: guard here is: an open `[a-z]+` there would swallow a NOUN, which is exactly the
#: extraposed shape the pronoun branch above refuses (`is it clear legal retracted` —
#: `clear` must not read as an adverb). `really|still|actually|just` are the round-18
#: additions, and each one exists so the pronoun branch can stay STRICT (a pronoun is
#: the whole subject) without losing a legitimate question: `is it really retired` and
#: `is the export still archived` route, measured. `probably`/`likely` are NOT here —
#: they are the extraposed frame's own adverbs (`is it likely legal retracted`), and
#: admitting them would re-open the class this slot is closed against.
#: ⛔ AND `now` IS DELIBERATELY ABSENT even though it is the commonest adverb here: it
#: is ALREADY a member of the SUBORDINATOR lookahead above (`now the contract is
#: archived` is exactly the subordinate-clause shape that guard exists to refuse, and
#: that guard requires a PRECEDING WORD, so a bare-pronoun subject is refused too).
#: Adding it here would be dead code that reads as coverage — measured:
#: `is the export now archived` is REFUSED by the subordinator guard, not routed, and
#: `is it now retired` likewise. Recorded rather than duplicated.
#: ⛔ THE HEAD AUXILIARY MAY CARRY ITS CLITIC (round-4 review, P2, measured): the head was
#: spelled `is|are|was|were` / `has|have|had` with nothing between the verb and the space, and
#: `\b` cannot match after `is` inside `isn't` — so a refused-and-reporting pair became a silent
#: one. `isn't the export archived` and `hasn't the export been archived` came back
#: `population=None, signal=None` while their UN-contracted twins `is the export not archived`
#: and `is the export archived` ROUTE (measured: over `8 subjects x 7 endings`, 384 of 384
#: contracted probes silent and 384 of 384 uncontracted twins routed). A negative question is
#: still a question about a state, and the ended population answers it the same way it answers
#: the uncontracted spelling, so the two must not differ — routing, not a notice, is the parity.
#: The typographic apostrophe is accepted for the same reason as `:data:`_CONTRACTED_AUX``: it is
#: the same word on a phone keyboard.
#: ⛔ AN INFINITIVE OR PROGRESSIVE COPULA IS A SECOND VERB — **on the copula branch only**
#: (review P1 at `42e0c1a662`, measured). `is the export to be archived` (scheduled /
#: FUTURE state), `is the data being archived` (progressive), `is the export yet to be
#: archived` (the OPPOSITE of archived), `is the export supposed to be archived` / `…
#: going to be …` / `… due to be …` all serve the whole ended population, which cannot
#: contain a row that is pending-but-not-yet-ended — so the caller reads a full
#: population as "no", with `yet to be` answered upside down, and no notice. Measured:
#: 240 of 240 templated `<is|are> <subject> <infinitive> <ending>` probes routed with no
#: signal. A `to` that is a PREPOSITION rather than an infinitive (`is the export to
#: Europe archived`) carries no `be`, so it still routes.
#: ⛔ IT IS APPLIED HERE, NOT IN `_COPULA_SUBJECT`, because that guard is SHARED with the
#: perfect branch, where the predicate is already anchored by `has … been` and a
#: `to be <participle>` inside the subject is only a POST-NOMINAL MODIFIER — excluding it
#: there refused `has the contract to be signed been archived` and its siblings SILENTLY.
_COPULA_INFINITIVE_REFUSAL = (
    # ⛔ THE CLASS, NOT A SPELLING — interpolated from `_NONFINITE_COPULA` (which also
    # covers `to get|become|became`, `getting`, and up to two modifiers before the verb),
    # so the two consumers cannot drift the way `to\s+be|being` did: the literal form
    # missed `is the export to eventually get archived` while the shared guard's richer
    # class refused it.
    # ⛔ AND IT MUST BE IN THE ENDING FRAME, NOT MERELY PRESENT (round-11 review, P2,
    # measured). The blanket form above refused the non-finite word at ANY position in the
    # subject, so an ATTRIBUTIVE `-ing` word — a participle doing a noun's work — took the
    # whole question down silently: `is the remaining invoice archived` went `ended` ->
    # `None`/`signal=None` (no notice, no route) for 120 of 120 rows, and the same for
    # `keeping`/`staying`/`becoming` (30/30 each). That is a route -> silence move, the ONE
    # direction this widening must not take, and the perfect branch was unaffected
    # (`has the remaining invoice been archived` still routes) — the signature that this
    # guard alone was responsible. The refusal is therefore FRAME-AWARE, exactly as arm B
    # already is: the non-finite verb must be FOLLOWED (within FOUR adverbs) by an ending
    # word. ⛔ THE BOUND IS FOUR BECAUSE THE PREFIX ABSORBS FOUR (round-12 review, P1,
    # measured): `_COPULA_PREFIX` carries `{0,3}` subject words PLUS one adverb slot before
    # the ending, so a refusal bounded at two let THREE adverbs (`is the export being really
    # actually already archived`) fall through the gap and the prefix then carried the row to
    # the ending — 216 of 216 such rows ROUTED the whole ended population with `signal=None`
    # and no notice, the fail-open direction. A guard narrower than the frame it guards is
    # not a guard: the bound must match the consumer's, not a guess at the prose.
    # ⚠️ AND "FOLLOWED BY AN ENDING WORD" IS WEAKER THAN "CARRYING THE PREDICATE" (round-12
    # review, P3, measured): an ENDING WORD can itself be a noun-modifier, so
    # `is the remaining archived report archived` is refused with NO notice — the attributive
    # `-ing` followed by an attributive participle. That residual is pinned in `NOT_SELECTED`,
    # not claimed away: this rule discriminates on POSITION, and position cannot tell a
    # predicate participle from a modifying one.
    rf"(?!(?:[a-z0-9][a-z0-9'\u2019._/-]*\s+){{0,6}}{_NONFINITE_COPULA}(?![-/])\b"
    rf"{_COPULA_FRAME_ADVERB_SLOT}"
    rf"(?:{_ENDING_WORDS})\b)"
)
_COPULA_PREFIX = (
    rf"^(?:(?:is|are|was|were)(?:n['’]t)?\s+{_COPULA_INFINITIVE_REFUSAL}{_COPULA_SUBJECT}"
    rf"\s+(?:(?:already|ever|really|still|actually|just)\s+)?(?:{_ENDING_WORDS})\b"
    rf"|(?:has|have|had)(?:n['’]t)?\s+{_COPULA_SUBJECT}"
    rf"\s+been\s+(?:{_ENDING_WORDS})\b)"
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
#: ⛔ ``which_names_an_ending`` IS ONE OF THE HAND-WRITTEN ROWS — with
#: ``existence_unguarded``, ``which_modified_noun`` and ``is_copula_long_subject``, the
#: four the module's notice-scope paragraph names — and it is the ATTRIBUTIVE
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
#: A ``which`` question whose head noun is MODIFIED (`which customer claims were
#: archived`) is the recall `_WHICH_PREFIX` gives up on purpose — one head noun, so a
#: modifier cannot stand where a subordinate clause's verb would. It is still a state
#: question, so it is REPORTED here rather than silent: two head words, then the
#: wh-word's own auxiliary and predicate.
#: ⛔ The two-word window is what keeps the SUBORDINATE-CLAUSE class out —
#: `which vendor said our commitments were archived` has `said` where the auxiliary
#: must be, so it matches neither this row nor the shape.
_WHICH_MODIFIED_NOUN = re.compile(
    rf"^which\s+(?:of\s+)?"
    rf"(?:(?:our|the|all|your|their|its|my|these|those)\s+)?"
    rf"[a-z0-9][a-z0-9'’._/-]*\s+[a-z0-9][a-z0-9'’._/-]*\s+"
    # ⛔ AND THE AUXILIARY SLOT IS THE `_CONTRACTED_AUX` CLASS, NOT FOUR HAND-TYPED WORDS
    # (fresh review, P2, measured). This row is the fourth copy of the finite-auxiliary
    # class, and its drift was a SILENCE: the row insists the ending participle be preceded
    # by the wh-phrase's own auxiliary, so a CONTRACTED auxiliary failed to match and the
    # question was neither routed nor reported —
    #
    #   `which customer claims has been archived`     -> reported `which_modified_noun`
    #   `which customer claims hasn't been archived`  -> `population=None, signal=None`
    #
    # with the typographic twin (`hasn’t`) silent too. Measured after interpolating the
    # class: 1 920 probes move SILENT -> REPORTED, 0 move in any other direction (this row
    # is a notice row, so it can only ever ADD a report).
    rf"(?:{_CONTRACTED_AUX}|is|are|was|were|has|have|had)\s+(?:been\s+)?(?:{_ENDING_WORDS})\b"
)

#: A copula question whose SUBJECT is longer than the shape's four-word bound
#: (`is the onboarding flow for enterprise partners retired`). The shape refuses it —
#: a longer subject is where a clause can hide — but the question is still about a
#: state, so it is reported instead of silent.
#: ⛔ THE NEGATIVE LOOKAHEAD IS THE DISCRIMINATOR, and it is the same rule the bound
#: enforces structurally: if the subject carries a SUBORDINATOR (`because`, `that`,
#: `given`, `since`, `when`, `if`, …) OR A SECOND AUXILIARY/COPULA FROM THE LIST
#: BELOW (`is`, `was`, `has`, `did`, `will`, …) then the ending participle is that
#: clause's predicate, not the question's, so nothing is reported. The second-verb
#: half is measured, not defensive: `is there any chance the release was cancelled`
#: contains no subordinator and was reported without it. ⛔ IT IS A CLOSED LIST, NOT
#: "every finite verb" (round-12 review, P3, measured): a LEXICAL second verb is not
#: in it, so `is it possible the feature got archived` still reports
#: `is_copula_long_subject`. That leak is a notice on a content question — the safe
#: direction (a report, never a route) — and it is recorded rather than claimed
#: closed.
#: ⛔ AND IT CARRIES TWO ARMS, because the shape can refuse a SHORT subject too (review
#: P1 at `42e0c1a662`, measured). Arm A is the long subject above. Arm B is the
#: INFINITIVE OR PROGRESSIVE SUBJECT — `is the export to be archived`, `is the data
#: being archived` — which the shape's own second-verb lookahead now refuses, and whose
#: subject is FOUR words or fewer, so arm A's `{5,}` never saw it: measured, both were
#: SILENT after the exclusion alone, which would have traded one #14033 defect (a route
#: with the qualifier dropped) for the other one (an unrouted state question that looks
#: like an honest empty answer). Arm B reports them: a report is advice on a refused
#: cue, never a route, and the population served stays the relevance read.
_COPULA_LONG_SUBJECT = re.compile(
    # ⛔ THE REPORTER MUST SEE EVERY HEAD THE REFUSAL REFUSES (round-13 review, P2, measured).
    # `_COPULA_PREFIX` accepts a CONTRACTED head — `(?:n[\'’]t)?`, fixed in round 4 — but this,
    # the ONLY notice arm for the copula branch, was anchored `^(?:is|are|was|were)\s+`, so a
    # contracted head could never report at all. A strong progressive cue then reached NO
    # ANSWER and said NOTHING: measured at `3088fd8d6a`, `isn\'t the export being archived`
    # answers `(None, None, None)` — refused, `signal=None`, no payload — and its adverb
    # variants likewise, while `is the export being archived` reports `is_copula_long_subject`.
    # One token (the contraction) decided whether the caller was told. This is #14033 itself on
    # the head this PR adds, so the anchor must be the class, not a spelling of it.
    rf"^(?:is|are|was|were)(?:n[\'’]t)?\s+"
    rf"(?:"
    # ARM A — the subject runs past the shape's four-word bound.
    rf"(?:(?:the|our|my|its|their|these|those|this|that|all|your)\s+)?"
    rf"(?!(?:[a-z0-9'’._/-]+\s+){{0,12}}"
    # ⛔ `and`/`or` are NOT in this list: they introduce no finite verb, and a
    # coordinated SUBJECT (`is the SOC2 audit and its evidence package archived`) is a
    # plain long noun phrase this row exists to report. A coordinated CLAUSE still
    # carries its own verb, which the list below already refuses.
        # ⛔ `now` IS NOT IN THIS LIST (round-12 review, P3, measured): it made the notice arm
    # FALL SILENT for a progressive frame with three adverbs — `is the export being really
    # actually definitely archived now` reported NOTHING while the same row without ` now`
    # reported `is_copula_long_subject`. A question ending in an adverb of time introduces no
    # finite verb, so it cannot be the second clause this list exists to detect; refusing to
    # report it only converted a notice into a silence. A report is the safe direction.
    rf"(?:because|that|given|since|when|while|if|unless|after|before|"
    rf"which|who|whom|whose|where|is|are|was|were|has|have|had|do|does|did|will|"
    # ⛔ NO `_CONTRACTED_AUX` HERE, AND THAT IS A MEASURED DECISION, NOT AN OVERSIGHT
    # (fresh review, P2). Everywhere else in this module the contraction class is
    # interpolated because leaving it out costs a notice. Here the list is a NEGATIVE
    # member — a second finite verb inside the subject means the ending participle is that
    # clause's, so NOTHING is reported — and interpolating the contractions was measured
    # converting 1 180 rows from `signal='is_copula_long_subject'` to `signal=None` (e.g.
    # `is the team the report hasn't been filed by archived`). A silent decline is the
    # direction #14033 exists to remove, so the un-contracted run stays and the resulting
    # asymmetry is stated: a contraction in the subject leaves the notice ON (the safe
    # direction, a report and never a route — the same trade this row's notice-leak note
    # records for a lexical second verb). Pinned by
    # `test_the_contraction_in_a_LONG_subject_stays_on_the_safe_side`.
    rf"would|can|could|may|might|must|should)\b)"
    rf"(?:[a-z0-9][a-z0-9'’._/-]*\s+){{5,}}"
    rf"(?:already\s+|ever\s+)?(?:{_ENDING_WORDS})\b"
    rf"|"
    # ARM B — the subject carries an infinitive or progressive copula, so the ending
    # participle is inside a scheduled/future (`to be archived`) or ongoing (`being
    # archived`) verb phrase rather than a state the ended population can answer. The
    # same subordinator/second-auxiliary negation applies: a clause that SUPPLIES the
    # predicate is still not this question's.
    # ⛔ THE LOWER BOUND IS ZERO, NOT ONE (fresh review, P3, measured): the REFUSAL
    # lookahead (`_COPULA_INFINITIVE_REFUSAL`) allows `{0,6}` words before the non-finite
    # verb, so with `{1,5}` here the ZERO-word endpoint — a SUBJECT-LESS non-finite question —
    # was refused with NO report, which is the silence this module's contract forbids and the
    # exact reason arm B exists. ⛔ THE MEASURED HISTORY, WITH THE COMMITS NAMED, because an
    # earlier cut of this note said "at the delta's base these ROUTED" and that is FALSE —
    # the base of the commit that fixed it was ALREADY silent (re-measured, all four rows):
    #   at `0e52593c91` (before `_COPULA_INFINITIVE_REFUSAL` landed)      -> ended / is_copula
    #   at `d8295ea8b2` (the refusal landed, arm B still `{1,5}`)          -> None / None / no payload
    #   at `cdfc3b1957` (the refusal + arm B `{0,5}`)                       -> None / is_copula_long_subject
    #   `is being archived` / `is to be archived` / `is getting archived` / `was being retired`
    # Enumerated class 4 auxiliaries x 6 non-finite forms x 5 endings = 120 rows. ⛔ ALL 120
    # gain `is_copula_long_subject` — an earlier cut of this note said "56 rows gain" one line
    # below the "= 120 rows" it was describing, which reads as 64 still unreported. Measured.
    # The verdict (refuse) is right — a scheduled or progressive state must not be served —
    # but the report must survive it. Measured after widening: 0 population changes across
    # 34,560 probes.
    rf"(?:(?:the|our|my|its|their|these|those|this|that|all|your)\s+)?"
    rf"(?!.*\b(?:because|that|given|since|when|while|if|unless|which|who|whom|"
    rf"whose|where)\b)"
    rf"(?:[a-z0-9][a-z0-9'’._/-]*\s+){{0,5}}{_NONFINITE_COPULA}"
    # ⛔ THE SAME SLOT AS THE REFUSAL, because the two consumers must move together
    # (round-12 review, P2, measured): widening only the refusal turns the fail-open into
    # SILENCE — `is the export being very archived` with the refusal widened and arm B left
    # narrow answers `(None, None, None)`, a strong cue reaching no answer and saying nothing.
    rf"{_COPULA_FRAME_ADVERB_SLOT}"
    rf"(?:{_ENDING_WORDS})\b"
    rf")"
    rf"{_BLOCKED_TAIL}"
)

_ENDING_SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    *(
        (name, re.compile(shape.pattern.replace(_TAIL_END, _BLOCKED_TAIL)))
        for name, shape in _ENDING_SHAPES
        if _TAIL_END in shape.pattern
    ),
    # ⛔ THE TRAILING CONTEXT IS A LOOKAHEAD, never part of the match (#14033 review).
    # `\s+[a-z]` INSIDE the pattern put the first letter of the next noun in
    # `group(0)`, so the cue a caller is handed was truncated mid-word — measured:
    # `which of our withdrawn claims affected pricing` reported
    # `'which of our withdrawn c'` through `missed_route_payload`'s `cue`, whose
    # contract is "``cue`` names the words it saw". Every other signal ends on a
    # `\b`; this row is the one that did not.
    # ⛔ A DETERMINER AFTER THE PARTICIPLE MEANS A VERB, NOT A MODIFIER (#14033
    # round-8 review, P2). `which cancelled the deal` / `which ended the argument` /
    # `which retired the old process` put a finite past-tense verb with its own
    # OBJECT where a modifier should be, and each carried the notice. A modifier is
    # followed by the NOUN it modifies, never by a determiner.
    ("which_names_an_ending", re.compile(
        rf"^which\s+(?:of\s+)?"
        rf"(?:(?:our|the|all|your|their|its|my|these|those)\s+)?"
        rf"(?:{_ENDING_WORDS})"
        # ⛔ THE DETERMINER CLASS IS CLOSED, AND IT USED TO BE TOO SHORT (round-14
        # review, P3, measured): `all|both|each|either|neither|another|other|such|one|
        # two|three|first|last|next` were missing, so the verb-with-object class this
        # lookahead exists to refuse still carried the notice on
        # `which retired all the processes` / `which cancelled both deals`. The
        # direction was the safe one (a notice, never a route), but the comment above
        # said "never by a determiner" and the row did not hold it.
        rf"(?=\s+(?!(?:the|a|an|this|that|these|those|its|their|our|my|your|his|her|"
        rf"any|some|every|no|all|both|each|either|neither|another|other|such|one|two|"
        rf"three|first|last|next)\b)[a-z])")),
    # …and the unguarded `existence` expression: a state question whose state cue is seen and
    # that no shape selects is reported, never silenced (silence is the defect).
    # ⛔ IT CARRIES THE TAIL GUARD, AND THAT IS THE ROUND-16 REVIEW'S P2 (measured):
    # without one it reported on ANY tail — `… been withdrawn most recently`,
    # `… been withdrawn only after the audit`, `… been withdrawn zzzz` all emitted a
    # notice with the cue truncated at `withdrawn` — which falsified the two paragraphs
    # above that say a qualifier OUTSIDE the closed tail list stays silent, and the
    # definition of "strong" at `:317-322`. Its stated job is "reports the cue wherever the shape
    # declined, and the TAIL decides whether the decline is worth a notice", so it is the shape's
    # tail that decides; every `NOTICE_ONLY` existence row ends at `$` or punctuation and reports.
    # ⛔ AND IT IS NOT "FAILS ONLY ON THE CLAUSE GUARD" (round-6 review, P3, measured): this row
    # carries `_EXISTENCE_HEAD` + the REFUSAL-SIDE middle slot + the blocked tail, so it also fires
    # where the guard PASSES and only the WINDOW's narrow recall slot fails — measured,
    # `has any we relied on been just withdrawn` has the guard passing (head+guard matches) and
    # still reports, declining on the recall slot rather than the guard. The two older paragraphs
    # above said otherwise; this is what the code does.
    # ⛔ THE NOTICE MUST SEE THE CLASS THE GUARD REFUSES ON (round-5 review, P3, measured): this
    # row carried `_EXISTENCE_WINDOW`, whose slot is the NARROW recall set (`already|ever|recently`),
    # while the guard itself refuses on the WHOLE `_EXISTENCE_ADVERB` class. So the same refused
    # frame reported on one spelling and went SILENT on the next (each needs the quantifier after
    # `has`, which `_EXISTENCE_HEAD` requires — measured, `billing has been …` alone reports
    # neither way, so these are the frames that discriminate):
    # `has anyone complained billing has been recently deprecated` → existence_unguarded, but
    # `has anyone complained billing has been just deprecated` / `… only deprecated` → refused,
    # `signal=None` — and the §"refusal reports" pins only passed because a trailing
    # `been <ending>` clause supplied their notice. A signal cannot affect routing (it is only
    # computed when NO shape applies), so widening the slot here cannot create a route.
    ("existence_unguarded", re.compile(
        "(?:"
        + _EXISTENCE_HEAD_NOTICE
        + rf"[^?]{{0,60}}?\bbeen\s+{_EXISTENCE_REFUSAL_ADVERB_SLOT}(?:{_ENDING_WORDS})\b"
        # ⛔ A CLITIC SUBJECT IS ALSO A CUE THAT MUST REPORT (round-8 review, P2, measured):
        # the copula/perfect shape refuses a clitic-bearing subject on purpose, and the
        # comment above says the row then "falls through to `_ENDING_SIGNALS`, where the
        # clitic alternative refuses it and reports" — true only when the question ALSO
        # carries an existential head. With a bare copula head there is nothing to fall
        # through TO, so `has the billing's been deprecated` returned NOTHING AT ALL
        # (no population, no notice) while its parent routed it `ended`: measured, all of
        # 8 determiners x 4 nouns = 32/32 silent refusals. A row with `been <ending>` in it
        # is a state cue; this arm is what breaks that silence. It is a SIGNAL arm, not a
        # shape, so it cannot route anything.
        + "|"
        # ⛔ AND THE HEAD IS THE MODEL'S ONE CONTRACTED-AUXILIARY CLASS (review, P2, measured):
        # the arm re-typed `has|have|had|is|are|was|were` with whitespace directly after it, so a
        # CONTRACTED head could not reach its own notice and the refusal went SILENT — the
        # module's worst direction. Measured over 60 pairs (5 heads x 3 subjects x 5 endings):
        # 60 of 60 contracted spellings were silent while the uncontracted twin reported.
        #   has the billing's been deprecated      -> existence_unguarded
        #   hasn't the billing's been deprecated   -> NOTHING
        # Same root as every other report arm this PR has de-duplicated: a hand-typed copy of a
        # closed class drifts from its one home.
        # ⛔ A MODAL PERFECT IS THE SAME CUE (round-32 review, P3, measured): the head class
        # was `has|have|had|is|are|was|were`, so `could the vendor's supply have been
        # archived`, `will the client's report have been archived` and `will billing's just
        # been deprecated` reported NOTHING — a strong state cue inside the class this arm's
        # own comment calls absolute. Signal-only, so it cannot route.
        + rf"\b(?:{_CONTRACTED_AUX}|has|have|had|is|are|was|were|"
        + r"will|would|should|could|can|may|might|must)\s+[^?]{0,40}?['’](?:s|ve|d)"
        # ⛔ THE CLITIC AND `been` NEED NOT BE ADJACENT (round-31 review, P1/P2, measured):
        # this arm required `['’](?:s|ve|d)` then `\s+been`, so ONE word between them
        # blinded it and the row was refused with NOTHING reported — `has billing's JUST
        # been deprecated` (the hijack frame itself, when its head is not `any`/`anything`),
        # `has support confirmed billing's just been retired`, and the
        # possessive-subject class whose noun collides with the adverb table or ends in
        # `-ly` (`has the client's SUPPLY been archived`, `has the vendor's PRACTICE been
        # archived`, `has the invoice's DATE been archived`). Measured on the previous
        # head: 2,400 of 5,600 possessive rows and 240 of 400 hijack rows silent.
        # A SIGNAL arm can only ever ADD a report — a signal is computed only after every
        # shape has declined — so widening the gap cannot route anything.
        # ⛔ THE SEPARATOR AND TOKEN CLASS ARE THE GUARD'S, NOT A NARROWER ONE (round-32
        # review, P3, measured): `[a-z0-9'’._/-]+` covered only bare words, so a comma, a
        # non-ASCII letter, `#` or `$` in the gap blinded the arm and the row stayed
        # SILENT — while the GUARD at `_COPULA_SUBJECT` already treats `,` as the same gap.
        # `has the vendor's recently, been deprecated`, `has the client's café been
        # archived`, `has the vendor's report #12 been archived` all returned no
        # population, no signal and no payload. One gap spelled in two places is how the
        # two drift apart; this arm takes any non-space token, which is what the
        # `[^?]{0,60}?` in the arm above it already means.
        + r"[,\s]+(?:[^?\s]+[,\s]+){0,3}been"
        # ⛔ THE WIDE REFUSAL SLOT, NOT THE NARROW WINDOW ONE (round-9 review, P2, measured): the
        # first cut of this arm used `_EXISTENCE_ADVERB_SLOT` (`already|ever|recently`), so the
        # adverb class the guard itself refuses on blinded it — `has the billing's been just
        # deprecated` and `… been only just deprecated` were still refused SILENTLY, one adverb
        # away from the frame this arm exists for. The row's first arm already uses the wide slot;
        # this arm now matches it.
        + rf"\s+{_EXISTENCE_REFUSAL_ADVERB_SLOT}(?:{_ENDING_WORDS})\b"
        # ⛔ AND `been` ITSELF IS FORBIDDEN AS A SUBJECT WORD, SO A BARE COPULA HEAD WITH ONE
        # HAS NOTHING TO FALL THROUGH TO EITHER (round-31 review, P3, measured): the round-30
        # fix added `(?!been(?![-/])\b)` to `_COPULA_SUBJECT` so the subject slot cannot
        # swallow the auxiliary — right — but that left `<is|are|was|were> <subject> been
        # <ending>` with NO notice: `is the export been archived`, `is the report been reviewed
        # archived`, `is the export still been archived` all came back `population=None,
        # signal=None, payload=None`, while the base `42e0c1a662` served `ended/is_copula` for
        # every one of them. Ungrammatical input, but the mechanism is the one this module
        # forbids — a cue reaching no answer and saying nothing — and it measured 45 of 45
        # `is <det> <noun> been <ending>` rows silent. They report `existence_unguarded` now.
        # ⛔ SCOPED TO THE COPULA HEADS ON PURPOSE (measured, first cut): `_COPULA_SUBJECT`'s
        # `been` exclusion lives in the `is|are|was|were` branch, so `<has|have|had> … been` is
        # NOT this silence — and letting the alternative take those heads made it PREEMPT
        # another row's signal: `which customer claims has been archived` is pinned to report
        # `which_modified_noun` and started reporting `existence_unguarded` instead, because this
        # row is evaluated BEFORE that one (index 6 of 9 vs 7 of 9; the round-32 review corrected
        # an earlier "FIRST in `_ENDING_SIGNALS`" here, which was false). Narrowed to the copula
        # heads, that row is untouched (the suite caught it, and the narrow scope is what it
        # measured).
        # ⛔ AND THE CONTRACTED COPULAS ARE THE SAME HEAD (round-32 review, P2, measured): the
        # first cut spelled `is|are|was|were` by hand, so EVERY contracted spelling stayed silent
        # — 108 of 108 `{isn't,aren't,wasn't,weren't} x {the,a,its} x {export,report,plan} x
        # {archived,deprecated,retired} been <ending>` rows returned `population=None,
        # signal=None, payload=None`, while the uncontracted twin reported: the same
        # hand-typed-copy drift the sibling arm above was widened to `_CONTRACTED_AUX` for, one
        # line over. The contraction is spelled HERE rather than by taking the whole
        # `_CONTRACTED_AUX` class on purpose: the auxiliary members (`hasn't`/`haven't`/
        # `hadn't`) must NOT enter this arm, because they are what preempts
        # `which_modified_noun` (measured both ways) — only the four copulas belong to this
        # silence.
        + r"|\b(?:is|are|was|were)(?:n['’]t)?\s+[^?]{0,40}?\bbeen(?![-/])\b\s+"
        # (one `-ed/-en` participle is allowed between `been` and the ending word: the refused
        # spelling that reached this arm was `is the report been reviewed archived`, the same
        # silence one word further out.)
        + r"(?:[a-z]+(?:ed|en)\s+)?"
        + rf"{_EXISTENCE_REFUSAL_ADVERB_SLOT}(?:{_ENDING_WORDS})\b"
        # ⛔ ARM C — `already|ever` AS THE AUXILIARY, with no `been` (turn-3, P2, measured —
        # see :data:`_ALREADY_EVER_AUX`). This is the class ``_ENDING_SHAPES`` refuses on
        # purpose since round 7 and used to refuse SILENTLY: `have any reports already
        # archived` / `have any reports ever archived` / `have any claims already retracted` /
        # `have any reports ever withdrawn` / `have any customers ever cancelled` were
        # `population=None, signal=None` at `34b339c75d`, every one of them `ended`/`existence`
        # on `main` `70fbd1a27d`. The two older arms cannot cover it: both require `been`, and
        # in this class the adverb IS the auxiliary. A SIGNAL cannot ROUTE — the refusal itself
        # stays exactly as the decision left it — but ⛔ IT IS NOT "ONLY ADDS A NOTICE" EITHER
        # (fresh review, P2 at turn 3, measured): the signal rows are ORDERED and this row sits
        # BEFORE ``which_modified_noun``, so a COMPOUND question that matched a later row now
        # reports THIS name instead — `which customer claims has been archived and have any
        # reports already archived` was `which_modified_noun` (`cue` = `which customer claims has
        # been archived`) and is `existence_unguarded` (`cue` = `have any reports already
        # archived`), with POPULATION and HINT unchanged. Both names are a notice about the same
        # question, so the effect is a NAMING one, and it is pinned rather than described as
        # additive. Ordering it after ``which_modified_noun`` would trade this for a count this
        # module pins (`at MOST 18 compiled patterns`) and for the row's own first-match contract.
        # ⛔ AND THE ATTRIBUTIVE CONTENT LOOKUP IS SILENT ONLY IN THE ADJACENT SPELLING (fresh
        # review, P2, measured): `been` is not a member of :data:`_BLOCKED_TAIL`, so
        # `have any reports already archived been published` — an ending word MODIFYING the
        # quantifier's noun, the question's own predicate being `published` — fails the tail and
        # reports nothing. But a QUALIFIER between the two (`… already archived in Q3 been
        # published`, `… by legal been published`) puts a tail MEMBER there, so it DOES report.
        # That is not new to this arm: the pre-existing `been` arm reports the same shape on
        # `main`'s head (`have any reports been archived in Q3 been published` -> `existence`).
        # The residual is a notice on a content query (the safe direction) and is pinned.
        + "|"
        + _EXISTENCE_HEAD
        + rf"[^?]{{0,60}}?\b{_ALREADY_EVER_AUX}\s+(?:{_ENDING_WORDS})\b"
        + ")"
        + _BLOCKED_TAIL)),
    ("which_modified_noun", _WHICH_MODIFIED_NOUN),
    ("is_copula_long_subject", _COPULA_LONG_SUBJECT),
)

#: What a caller is told when a state cue was seen and no shape applied. ONE
#: string, owned here, so the read path, the MCP description and the routing test
#: cannot drift into describing the boundary differently — the drift #14033 is
#: about ("the feature exists but did not fire, and nothing says so").
#: ⛔ THE FIRST CLAUSE SAYS WHAT WAS SEEN, NOT WHAT THE QUESTION IS (round-13
#: review, P3). It used to read "this question names an ENDING state", which the
#: served ``ROUTING_NOTE`` contradicts for the ATTRIBUTIVE rows: `which archived
#: report has the Q3 numbers` fires the `which_names_an_ending` SIGNAL (cue
#: `which archived`) while the note files that phrasing under DOES NOT ROUTE
#: because the ending word MODIFIES its noun and the question's predicate is
#: `has the Q3 numbers`. An ending CUE was seen — that is what a signal means and
#: it is what the payload reports — and the last paragraph here is the branch that
#: tells an attributive caller the relevance read is the right one.
MISSED_ROUTE_HINT = (
    "a strong ENDING cue was seen in this question, but no state-question shape "
    "applied to it, so it was answered by the RELEVANCE read — an empty or "
    "unrelated reply here is NOT evidence that nothing has ended. If the ENDING "
    "is what you are asking about, ask it as a whole question (\"what ended\", "
    "\"is <record> "
    "retired?\", \"list what we retired\") or as an unqualified set (\"which "
    "<records> were retracted\"); every row of the ended population states how it "
    "ended (status, successorId/successorName, stage+outcome, endedAt). If the "
    "QUALIFIER is what you are asking about (\"in v3\", \"by legal\", \"in "
    "Q3\"), this relevance read is the right one and the population cannot apply it."
)

#: WHICH PHRASINGS ROUTE — the caller-facing predictability block of #14033, owned
#: here for the same reason :data:`MISSED_ROUTE_HINT` is: the SERVED text, the
#: developer prose in ``mcp_server.tortoise_search`` and the routing test must not
#: be able to describe the boundary differently.
#:
#: ⛔ IT IS SERVED FROM ``tool_registry`` — NOT from a docstring. `FastMCPAdapter.
#: register_all` hands FastMCP ``ToolDefinition.description`` and an explicit
#: ``description=`` OVERRIDES the docstring (gofastmcp.com *Tools*: "when provided,
#: FastMCP ignores the function docstring for the tool description"), so prose added
#: to ``tortoise_search.__doc__`` alone reaches NO caller — which is exactly what
#: happened when this block first landed and the defect #14033 names stayed live on
#: every MCP surface. The test reads the served string, not the docstring.
ROUTING_NOTE = """\
⛔ WHICH PHRASINGS ROUTE, SO YOU CAN PREDICT IT (#14033). The cue is the
question's own PREDICATE, anchored to its grammatical role — never an ending
word that happens to sit near a wh-word — so the boundary is readable off the
wording. Of the phrasings this read is asked about:

  ROUTES — answered from the ended population, each row with how it ended:
    `has anything we relied on been withdrawn`   (has/have + quantifier + been)
    `what ended`                                 (the whole question is the ending)
    `what has ended recently`                    (a RECENCY adverb only: it names
                                                  no window the read must apply,
                                                  and every row carries `endedAt`)
    `is the onboarding flow retired?`            (yes/no, subject = the record)
    `list what we retired`                       (imperative over `what we …`,
                                                  i.e. a request for a SET)
  DOES NOT ROUTE — you get the relevance rows, and ONLY the rows change:
    `which of our withdrawn claims affected pricing`
                                                 (the ending word MODIFIES
                                                  `claims`; the question's own
                                                  predicate is `affected
                                                  pricing`, which the
                                                  population cannot answer)

⛔ SO A STATE QUESTION THAT DOES NOT ROUTE BEHAVES EXACTLY LIKE A NORMAL
SEARCH. There is no second attempt and no fallback: you get the rows the words
match, which may be empty or unrelated, and an EMPTY answer here is NOT
evidence that nothing has ended. This tool takes NO argument that forces the
ended read — the SHAPE is the only way in, so the way to be explicit is to ask
a form from the ROUTES list above. ⛔ AND A RETRIEVAL ARGUMENT THAT CHANGES THE
READ SILENCES THE REPORT, which is the other half of that boundary: a
NON-DEFAULT retrieval argument asks for a FILTERED relevance read, and the cue
report is raised inside the same guard that decides routing — so with any of
them supplied you get that filtered read and NO report, and a state question
you filtered looks exactly like an honest empty again. (A value equal to the
default — `threshold=0`, `min_confidence=0`, `order_by="relevance"` — is not a
filter and does not silence it.) ⛔ THE CLOSED LIST IS THE AUTHORITY, NOT THIS
SENTENCE: `_retrieval_knobs` in `tortoise/sdk.py`, and a name given here — a
non-default `threshold`, `min_confidence`, `order_by`, `relationship_filter`,
`traversal_path`, `pool_size`, `time_aware` — is an EXAMPLE of it, never the
set. Only THREE parameters sit OUTSIDE it: they change WHICH rows a read
returns rather than asking for a filtered read, because the POPULATION applies
them — `kind`, `entity_type`, `limit` — so none silences the notice. The
filter and the population are different answers: send the question ALONE to
get the population or the report. (The
hosted `GET /v1/search` route below takes only the question and `limit`,
neither of which is a routing argument, so it is not silenced.)
⛔ THIS TOOL CANNOT REPORT the cue it saw
and could not apply — its reply is the row list and it takes no sink — so an
MCP caller who needs that report must use the HOSTED deployment's
`GET /v1/search` route, whose response body carries `state_question`
(`{routed: false, population, reason, cue, hint}`), which is the only
CUSTOMER-facing channel: no PUBLIC SDK method exposes it either — the same
payload rides the PRIVATE
`TortoiseSDK._tortoise_fts_query(..., read_status_out=...)` sink (under
`read_status_out["state_question"]`), so an integration that calls the SDK
directly must name that private method deliberately. ⛔ AND THE HOSTING MATTERS
rather than being a detail of the path: the SELF-HOSTED `GET /v1/search` (the
same path, `selfhost_api.py`) recognises the cue too — routing happens inside
the SDK call — but it passes NO sink and answers with the bare row list, so on
a self-hosted deployment a declined state question is still indistinguishable
from "nothing has ended"; the private SDK sink above is the only way to read it
there — and what that sink buys is the distinction between "the route declined
my state question" and "the graph says nothing has ended".
"""

#: ⛔ AND THE FAMILY ARM NEEDS ITS OWN SENTENCE (round-7 review, P2): there a shape
#: DID apply, and the remedy is NOT "ask the whole question" — the caller already did,
#: and it will be refused again for the same family. Telling them so is the difference
#: between a notice and a runaround.
MISSED_ROUTE_FAMILY_HINT = (
    "this IS a state question, but the ended population has no arm for this "
    "entity_type: that family has no lifecycle (an Event does not — design §8), so "
    "the relevance read answered instead. Re-read it with a family that has one "
    "(entity_type='point', 'object' or 'subject'); every row of the ended population "
    "states how it ended (status, successorId/successorName, stage+outcome, endedAt)."
)

#: The reasons a strong cue can be seen without the population being served.
#: CLOSED, because a caller branches on the value: ``shape`` is the guard refusing
#: the question (an appended constraint, an attributive participle), ``family`` is
#: a shape that DID fire on a family the ended population does not exist for (an
#: Event has no lifecycle — ``ended.py``'s own refusal).
MISSED_ROUTE_REASONS = ("shape", "family")

#: The keys :func:`missed_route_payload` writes — the CLOSED list a caller-owned sink
#: must be cleared of before a new decision is written to it. It is a constant because
#: the clearing side (`sdk._tortoise_fts_query`) is in another module and a sink is
#: CALLER-OWNED and reusable: it may carry the caller's own bookkeeping beside these
#: keys, so the sink is cleared BY KEY and never with `dict.clear()` (the same hygiene
#: `truncation.mark_truncation` performs on its sibling sink). A key added to the
#: payload without being added here would leave a stale one behind on a reused sink —
#: which is the whole reason the clear exists — so :func:`missed_route_payload` asserts
#: the two agree and a test pins it.
STATE_NOTICE_KEYS = ("routed", "population", "reason", "cue", "hint")


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
    shape. ⛔ IT IS THE MATCHED TEXT **AFTER NORMALISATION**, not the caller's own
    spelling: the detector lower-cases, collapses whitespace, and translates the
    apostrophe inventory to ASCII before it matches, so a caller who typed `hasn’t`
    is shown `hasn't`. That is deliberate — the cue is what the PATTERN saw — but a
    caller comparing it against their own string will not find it, so the contract
    says so here rather than leaving the difference to be discovered.
    """
    population: str | None = None
    shape: str | None = None
    cue: str | None = field(default=None)
    #: The strong cue that was seen when NO shape fired, naming the SIGNAL ROW that
    #: reported it (:data:`_ENDING_SIGNALS`) — a shape's own name, or one of the four
    #: hand-written recall rows that have no shape (`which_names_an_ending`,
    #: `existence_unguarded`, `which_modified_noun`, `is_copula_long_subject`), so a
    #: consumer must not assume the value is a :data:`_ENDING_SHAPES` name. ``None``
    #: when a shape fired, or when nothing state-like was seen at all. ⛔ It NEVER changes
    #: ``population``: a signal is a REPORT, not a route, which is what keeps the
    #: precision contract ("where a shape could go either way it does not fire")
    #: intact while removing the silence #14033 is about.
    signal: str | None = None
    #: The matched text of that signal, so a caller can say WHICH words it saw.
    signal_cue: str | None = field(default=None)


#: The apostrophe INVENTORY normalised before any pattern runs (round-27 review, P1,
#: measured). `_EXISTENCE_GUARD`'s contraction class is closed over `'` (U+0027) alone,
#: while the product routinely receives the typographic forms — `tortoise/extractor_v2.py`
#: normalises the same inventory before tokenising for exactly that reason, and this
#: module's own copula lookahead admits `\u2019` beside `'` (one token class; the
#: class is spelled with the escape, so it is the `['’]` suffix of the word slot in
#: `_COPULA_SUBJECT` — pointed at by NAME rather than by line number, because a line
#: pointer is invalidated by the next comment inserted above it, which is exactly what
#: happened to the `:1219` this note used to carry);
#: the INVENTORY below is what makes the rest of the family reachable at all).
#: ⛔ ONE CONSTANT, so the test that pins the family cannot drift off it: the pin asserts
#: THIS tuple and exercises every member, which is what makes a codepoint added to the
#: normaliser without being pinned a RED instead of a silent gap (the round-27 fix shipped
#: with 4 of these 7 exercised, i.e. three silently droppable).
#: ⛔ THE INVENTORY IS `extractor_v2`'s, NOT SEVEN HANDSOME CODEPOINTS (fresh review, P1,
#: measured). The first cut of this constant carried the seven that read nicely and its
#: docstring claimed parity with `tortoise/extractor_v2.py` — which normalises TWENTY-FIVE.
#: Measured: the eighteen it left out (`\u201a \u201b \u02b9 … \u00b4 \uff02 \ua78c`)
#: each still ROUTED the whole ended population with the caller's predicate dropped and
#: `signal=None`, i.e. the defect this constant exists to close, eighteen spellings over,
#: while the pin derived from the constant itself stayed green — a pin that reads the same
#: tuple it pins can never see the tuple is incomplete.
#: The set below is `extractor_v2._APOSTROPHES`' keys verbatim, and
#: `tests/test_13557_13558_reachable_reads.py` asserts PARITY against that table (not
#: against this tuple), so the two cannot drift and a codepoint added upstream reds here.
#: ⛔ Why a copy rather than an import: this module carries no project imports on purpose
#: (it is the pure detector several layers call), so the coupling is held by the parity
#: test instead of by an import edge.
_APOSTROPHES = (
    "\u2018", "\u2019", "\u201a", "\u201b", "\u02bc", "\u02b9", "\u2032", "\u2035",
    "\uff07", "\u00b4", "\u02be", "\u02bf", "\u02c8", "\u055a", "\u05f3", "\uff02",
    "\ua78c", "\u02bb", "\u02bd", "\u02c0", "\u02c1", "\u02ca", "\u02cb", "\u02cc",
    "\u02d0",
)


def detect_state_question(text: str | None) -> StateQuestion:
    """Map a query to the state population it asks about (:data:`POPULATIONS`).

    Deterministic and bounded — one normalisation plus AT MOST 18 compiled
    patterns in three passes that stop at the first shape that fires: the 6
    ending shapes, then (only if none fired) the 9 missed-cue signals (5 derived
    from those shapes + 4 hand-written recall rows, :data:`_ENDING_SIGNALS`),
    then the 3 commitment shapes
    (:data:`_COMMITMENT_SHAPES`). No model call and no IO, so the hot read path
    pays microseconds and the mapping is unit-testable offline.

    ⛔ THE THREE NUMBERS ABOVE ARE PINNED, not decorative: a row added to any
    of those tables reds
    ``tests/test_13557_13558_reachable_reads.py::test_the_bounded_cost_claims_name_every_scan``
    until this sentence is updated with it. A cost claim that drifts off the
    code it describes is how this module — whose job is to be the ONE home for
    the routing contract — shipped a stale count of its own scans. A ``None``/empty/whitespace query
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
    # ⛔ THE APOSTROPHE IS NOT ONE CHARACTER, AND THIS IS THE ONE CONSUMER THAT SEES THE
    # CALLER'S RAW TEXT (round-27 review, P1, measured). The contraction class is closed
    # over `'` (U+0027), but the product routinely RECEIVES U+2019 — `tortoise/
    # extractor_v2.py` normalises the whole `‘’‚‛` inventory before tokenising for exactly
    # that reason, and this module's own word classes admit `’` beside `'` (the class is
    # `['’]`, spelled from `_APOSTROPHES` — ⛔ NO COUNT HERE: an earlier cut of this note said
    # "eleven places" and its sibling said "eighteen", and the file carries neither number
    # consistently, so the count was replaced by the constant that decides it).
    # `detect_state_question` receives the string from `sdk.py` untouched, so
    # `have any users reported the vendor hasn’t been deprecated` (U+2019) routed the whole
    # ended population with the caller's predicate dropped and `signal=None` — 384 of 384
    # rows in a 6-spelling × 8-subject × 8-verb population — while its ASCII twin was
    # refused. One normalisation step closes the family, so it is done HERE rather than by
    # widening every alternation with a second apostrophe.
    _t = str(text).lower()
    for _apostrophe in _APOSTROPHES:
        _t = _t.replace(_apostrophe, "'")
    t = " ".join(_t.split())
    for shape, pattern in _ENDING_SHAPES:
        m = pattern.search(t)
        if m:
            return StateQuestion(ENDED, shape, m.group(0))
    # ⛔ THE SIGNAL IS CHECKED BEFORE THE COMMITMENT SHAPES, and a commitment
    # shape still WINS THE POPULATION — and, when it does, the cue is DROPPED. That is
    # a STATED LIMIT rather than a silent loss (#14033 round-14 review, P3, measured):
    # `what are we committed to and which claims were retracted by legal` comes back as
    # `committed_to` with ``signal=None`` and no payload, because a notice means "no
    # population was served" and a commitment population WAS served — so the ending ask
    # in the same sentence is not reported, and the caller who also wanted the ended set
    # has to ask it as its own question. The alternative (carrying `signal` on the
    # COMMITMENTS StateQuestion so the sink can say both) is a read-path change, not a
    # wording one, and it is recorded here as the boundary of the absolute above. A
    # commitment question that merely CONTAINS an ending word in somebody else's clause
    # (`what did the vendor say about our commitments`) matches no signal either — every
    # signal is a shape's own expression, plus the attributive `which` row.
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
    payload = {
        "routed": False,
        "population": population,
        "reason": reason,
        "cue": cue,
        "hint": (MISSED_ROUTE_FAMILY_HINT if reason == "family"
                 else MISSED_ROUTE_HINT),
    }
    if tuple(payload) != STATE_NOTICE_KEYS:  # pragma: no cover - drift guard
        raise AssertionError(
            f"missed-route payload keys {tuple(payload)} do not match "
            f"STATE_NOTICE_KEYS {STATE_NOTICE_KEYS}; a sink cleared by KEY would "
            f"carry the difference as a STALE notice on a reused dict")
    return payload


__all__ = [
    "COMMITMENTS",
    "ENDED",
    "MISSED_ROUTE_FAMILY_HINT",
    "MISSED_ROUTE_HINT",
    "MISSED_ROUTE_REASONS",
    "POPULATIONS",
    "ROUTING_NOTE",
    "STATE_NOTICE_KEYS",
    "StateQuestion",
    "detect_state_question",
    "missed_route_payload",
]
