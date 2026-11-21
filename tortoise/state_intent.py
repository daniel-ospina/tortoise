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
_BLOCKED_TAIL = (
    r"(?=\s*(?:[,;:—–]|[?.!]|$|\b(?:in|on|at|by|for|from|with|to|of|about|over|"
    r"against|regarding|concerning|toward|towards|and|or|but|so|yet|that|which|"
    r"when|where|who|if|as|than|because|while|after|before|during|since|until|"
    r"per|last|next|this|these|those|yesterday|today|tomorrow|recently|lately|"
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
_EXISTENCE_HEAD = (
    r"\b(?:has|have|had)\s+(?:any|anything|someone|something|everything|anyone)\b"
)
_EXISTENCE_GUARD = (
    r"(?![^?]{0,60}?\b(?:because|since|when|while|after|before|now|if|unless|given|"
    r"as|where|so|but|although|though|until|whether|once|whereas|why|how)\b)"
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
    r"(?:has|have|had|is|are|was|were|will|would|can|could|did|does|do|may|might|"
    r"must|should)\s+(?:not\s+)?(?:have\s+)?been\b)"
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
    r"(?:has|have|had|is|are|was|were|will|would|can|could|did|does|do|may|might|"
    rf"must|should)\s+(?:not\s+)?(?:have\s+)?been\s+(?:{_ENDING_WORDS})\b)"
)
_EXISTENCE_WINDOW = rf"[^?]{{0,60}}?\bbeen\s+(?:{_ENDING_WORDS})\b"
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
    #     routing. The recall given up is the subject whose tail is another NP
    #     (`is our v3 export the one archived`), which is a yes/no about ONE record and
    #     is now REPORTED rather than routed.
    # ⛔ AND A SECOND AUXILIARY INSIDE THE SUBJECT IS THE SAME STRUCTURE (round-16
    # review, measured): `has anyone complained billing has been deprecated` put its
    # own verb (`complained`) plus a whole asyndetic clause (`billing has been …`)
    # inside the perfect branch's subject, ROUTED to the whole ended population and
    # dropped the caller's predicate. An auxiliary in the subject means a nested
    # clause — the copula the shape needs is the one AFTER the subject — so the same
    # `{1,5}`-words-then-token rule is extended over the auxiliary list. ⛔ BOTH SETS
    # ARE ONE GUARD, not two: two overlapping lookaheads would let a later change drop
    # one silently (each masks a row the other catches).
    r"(?!(?:[a-z0-9][a-z0-9'’._/-]*\s+){1,5}"
    r"(?:that|which|who|whom|whose|whether|because|since|when|while|if|unless|given|"
    r"where|whereas|after|before|now|what|how|why|has|have|had|is|are|was|were|will|"
    r"would|can|could|may|might|must|should|do|does|did)\b)"
    r"(?!(?:[a-z0-9][a-z0-9'’._/-]*\s+){1,5}"
    r"(?:the|a|an|our|my|its|their|these|those|this|that|all|your)\b)"
    r"(?:(?:the|our|my|its|their|these|those|this|that|all|your)\s+)?"
    r"[a-z0-9][a-z0-9'’._/-]*(?:\s+[a-z0-9][a-z0-9'’._/-]*){0,3}"
)
#: ⛔ THE PERFECT BRANCH REQUIRES `been`, AND THAT IS A HIJACK FIX (round-7 review,
#: P1). `already`/`ever` in place of `been` reads as an ACTIVE third-party action —
#: `has the partner already cancelled` asks what the partner did, not whether the
#: record ended — and the earlier cut routed it (and `has legal already retracted`),
#: serving the whole ended population with no notice. Only `is|are|was|were` can be
#: followed by a bare participle, because there the passive/adjectival reading is
#: forced.
_COPULA_PREFIX = (
    rf"^(?:(?:is|are|was|were)\s+{_COPULA_SUBJECT}"
    rf"\s+(?:already\s+|ever\s+)?(?:{_ENDING_WORDS})\b"
    rf"|(?:has|have|had)\s+{_COPULA_SUBJECT}"
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
    rf"(?:is|are|was|were|has|have|had)\s+(?:been\s+)?(?:{_ENDING_WORDS})\b"
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
_COPULA_LONG_SUBJECT = re.compile(
    rf"^(?:is|are|was|were)\s+"
    rf"(?:(?:the|our|my|its|their|these|those|this|that|all|your)\s+)?"
    rf"(?!(?:[a-z0-9'’._/-]+\s+){{0,12}}"
    # ⛔ `and`/`or` are NOT in this list: they introduce no finite verb, and a
    # coordinated SUBJECT (`is the SOC2 audit and its evidence package archived`) is a
    # plain long noun phrase this row exists to report. A coordinated CLAUSE still
    # carries its own verb, which the list below already refuses.
    rf"(?:because|that|given|since|when|while|if|unless|after|before|now|"
    rf"which|who|whom|whose|where|is|are|was|were|has|have|had|do|does|did|will|"
    rf"would|can|could|may|might|must|should)\b)"
    rf"(?:[a-z0-9][a-z0-9'’._/-]*\s+){{5,}}"
    rf"(?:already\s+|ever\s+)?(?:{_ENDING_WORDS})\b{_BLOCKED_TAIL}"
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
    # …and the unguarded `existence` expression: a state question that fails ONLY on
    # the clause guard above is reported, never silenced (silence is the defect).
    # ⛔ IT CARRIES THE TAIL GUARD, AND THAT IS THE ROUND-16 REVIEW'S P2 (measured):
    # without one it reported on ANY tail — `… been withdrawn most recently`,
    # `… been withdrawn only after the audit`, `… been withdrawn zzzz` all emitted a
    # notice with the cue truncated at `withdrawn` — which falsified the two paragraphs
    # above that say a qualifier OUTSIDE the closed tail list stays silent, and the
    # definition of "strong" at `:317-322`. Its stated job is "fails only on the clause
    # guard", so it is the shape's tail that decides; every `NOTICE_ONLY` existence row
    # ends at `$` or punctuation and still reports.
    ("existence_unguarded", re.compile(
        _EXISTENCE_HEAD + _EXISTENCE_WINDOW + _BLOCKED_TAIL)),
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
NON-DEFAULT `threshold`, `min_confidence`, `order_by`, `relationship_filter`
or `traversal_path` asks for a FILTERED relevance read, and the cue report is
raised inside the same guard that decides routing — so with any of those
supplied you get that filtered read and NO report, and a state question you
filtered looks exactly like an honest empty again. (A value equal to the
default — `threshold=0`, `min_confidence=0`, `order_by="relevance"` — is not a
filter and does not silence it.) ⛔ AND THE TOOL'S OTHER THREE RETRIEVAL
ARGUMENTS ARE NOT IN THAT SET, which is why this sentence names its members
rather than saying "any argument": `kind`, `entity_type` and `limit` change
WHICH rows a read returns and are applied by the population itself, so they do
NOT ask for a filtered relevance read and leave the report intact — `kind=`
alone never silences the notice, and neither does `limit=`. The filter and the
population are different answers: send the question ALONE to get the population
or the report. (The
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
    t = " ".join(str(text).lower().split())
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
    return {
        "routed": False,
        "population": population,
        "reason": reason,
        "cue": cue,
        "hint": (MISSED_ROUTE_FAMILY_HINT if reason == "family"
                 else MISSED_ROUTE_HINT),
    }


__all__ = [
    "COMMITMENTS",
    "ENDED",
    "MISSED_ROUTE_FAMILY_HINT",
    "MISSED_ROUTE_HINT",
    "MISSED_ROUTE_REASONS",
    "POPULATIONS",
    "ROUTING_NOTE",
    "StateQuestion",
    "detect_state_question",
    "missed_route_payload",
]
