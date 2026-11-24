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
population. So an ending population is selected by *question shapes* — most of
them AGGREGATIVE (a wh-word or an "anything/which" quantifier carrying an ending
word), which is exactly the shape a customer uses when they do not know which
record they are asking about — and ⛔ NOT by a keyword, nor by "a word near a
wh-word", nor by "an ending copula anywhere in the sentence".

⛔ TWO NON-AGGREGATIVE SHAPES ARE ADMITTED, and #14033 is why. This paragraph used
to say a state question is *only* aggregative, and the list below then measured
what that claim cost: two of the most obvious ways a customer asks about state
fell through to the relevance read and were served as a confident answer to a
different question. They are ``is_predicate`` (`is the onboarding flow retired?`
— a yes/no question about ONE record the caller NAMES, whose PREDICATE is the
ending; the ended population is SERVED, so the named record's row carries its
``status`` when it lands on the page) and
``list_what`` (`list what we retired` — an imperative whose `what we <verb>`
CLAUSE asks for a SET). So "names a record" is no longer by itself a refusal:
`is the X retired?` routes because the participle is the question's predicate,
while `onboarding flow retired` (no predicate to ask about) and `show me the
retired onboarding flow` (an imperative naming ONE record) keep the text read
they always had.

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

#: One word of the subject NP.
#: ⛔ A hyphen or an apostrophe is an ordinary spelling INSIDE a head noun
#: (`company's`, `all-hands`, `e-mail`), not a clause boundary — a `[a-z0-9]+` run
#: refuses those spellings and answers `0 rows`, because the relevance read
#: excludes terminal statuses.
_NP_WORD = r"[a-z0-9][a-z0-9'-]*"

#: What a ``is_predicate`` subject NP's word run may NOT cross. A word run alone
#: admits a relative clause whose own finite verb then reads as the question's
#: predicate, and there are TWO spellings of that clause:
#:
#: * with an OVERT marker — `is the report that legal archived`, `is the ticket
#:   which support cancelled`, `is the doc where we archived it`;
#: * with NO marker at all — a zero relative, which is the same clause with the
#:   relativiser elided: `is the plan the team abandoned`, `is the data we
#:   retired`, `is the claim finance retracted`, `is the ticket they deprecated`.
#:   Measured on the marker-only guard: all four routed, i.e. the guard closed one
#:   spelling and left the other open.
#:
#: A clause can be neither the head noun nor a word inside it, so the run stops
#: at a relativiser AND at the subject a zero relative puts where the head noun's
#: own modifier would be — a determiner, a pronoun, or an indefinite/quantified
#: subject (`is the plan anyone abandoned`, `is the ticket both teams cancelled`).
#:
#: ⛔ AND IT STOPS AT A PREDICATE MODIFIER, which is a DIFFERENT failure from the
#: clause one: the words between the head noun and the ending can only modify the
#: PREDICATE, and each of the three classes below changes what the question asks.
#: Measured before this guard (PR #14491 review, P1/P2) — every row routed:
#:
#: * NEGATION (`not`, `never`, `neither`, `nor`, `longer`, the contracted
#:   auxiliaries matched as the SUFFIX `…n't`, and the approximatives
#:   `hardly`/`barely`/`scarcely`/`rarely`) — `is the plan not
#:   cancelled` was answered with the population of CANCELLED records, the
#:   polarity-INVERTED answer, which is the #14033 defect class exactly. `no`
#:   is left in the run by the residual below (it is an ordinary noun modifier),
#:   so the `no longer` spelling is stopped by its own token rather than by `no`;
#: * FUTURITY / INTENT (`to`, and the PROGRESSIVE `being`) —
#:   `is the v2 api to be retired` asks about something that has NOT ended, and
#:   the ended population can only answer it with records that have;
#:   `is the report being archived` is an action in flight, which the population
#:   cannot represent at all. ⛔ THE INFINITIVE IS STOPPED BY `to` ALONE, and the
#:   narrower list is measured rather than assumed: `be|going|about|due|scheduled`
#:   were stop words in the round-8 cut, and every one of its futurity rows still
#:   refuses without them (`to` sits between the head noun and the ending in each),
#:   while keeping them refuses two ordinary spellings for no closure at all —
#:   `is the annual due diligence report archived` and `is the current going
#:   concern note archived`, where `due`/`going` are head-noun modifiers in the
#:   second slot. `be` on its own had no falsifier at all: every infinitive that
#:   can reach this tail anchor already carries `to`;
#: * DISJUNCTION (`or`) — `is the ticket still open or cancelled` asks about two
#:   states at once, and one population cannot answer both.
#:
#: ⛔ NO RECALL IS LOST IN THE AFFIRMATIVE-MODIFIER CLASS, and that is measured
#: rather than assumed: `is the claim already archived`, `is the ticket still
#: archived` and `is the report already withdrawn` still route, and adding
#: `yet|still|already` as well would close the same class while refusing those
#: three — so this is the narrower list. ⛔ AND THE COST IS STATED, because a
#: linker word is also an ordinary NOUN: `is the do while loop deprecated` and
#: `is the before after comparison archived` keep the relevance read now. They are
#: rare spellings and the alternative is a polarity-inverted answer to a negation,
#: so the trade is taken here rather than hidden; the FRAME words (`to|due|
#: scheduled|going|about` introducing `be`) and the `of`-complement apply only in
#: their frame, which is why `is the go to market plan archived` and
#: `is the list of open tickets archived` still route
#: (``STOP_WORDS_KEEP_THIS_RECALL``).
#: ⛔ IT APPLIES FROM THE SECOND WORD ON, never to the head noun: `it` and `us`
#: are ordinary head nouns in this product's own vocabulary (`is our IT platform
#: retired`, `is the US footprint archived`), and the head-noun slot is not a
#: place a zero relative's subject can stand.
#:
#: ⛔ THE RESIDUALS ARE STATED, NOT HIDDEN, and there are five:
#:
#: * a zero relative whose subject is a BARE NOUN (`is the claim finance
#:   retracted`) is lexically identical to a two-word head noun plus a predicate
#:   (`is the onboarding flow retired?`), so no word list separates them without
#:   becoming the noun→route assignment #13128 refuses;
#: * `all|some|any|no` are left in the run on purpose — each is also an ordinary
#:   noun MODIFIER, and the cost is visible only in a non-head slot where it
#:   really does the modifying: `is the onboarding all hands meeting cancelled`
#:   routes at this head and refuses once `all` is a stop word. (A head-slot
#:   example would prove nothing: the stop never applies to word one.);
#: * so are the NUMERALS, for the same reason and in the same slot — the modifier
#:   use is `is the onboarding two day workshop cancelled` (which refuses once a
#:   numeral is a stop word), and the clause-subject spelling it keeps is
#:   `is the report one team archived`;
#: * AND THE SUBJECT NEED NOT NAME A RECORD AT ALL: a yes/no question about a
#:   TIME SPAN — `is the quarter ended`, `is the deadline ended`, `is the sprint
#:   ended` — routes, and the ended population answers a calendar question with
#:   our ended records. No word list separates `quarter` from a record name
#:   without becoming the same noun→route assignment, so this is disclosed rather
#:   than closed; the tail anchor still refuses the windowed spellings
#:   (`is the quarter ended in Q3`);
#: * AND A HEAD NOUN JOINED BY `or` IS REFUSED — `is the sales or marketing plan
#:   archived` keeps the relevance read. A coordinator INSIDE the NP is lexically
#:   identical to the disjunctive PREDICATE the `or` stop exists to close
#:   (`is the plan live or already cancelled`), and the precision contract says a
#:   shape that could go either way does not fire. The cost is not new either:
#:   `is_predicate` did not exist on the base module, which refused the same
#:   string. It is disclosed here so a later round does not present it as a find.

_NP_STOP = (
    r"(?!(?:that|which|who|whom|whose|where|when"
    r"|the|a|an|this|these|those|our|your|their|its|my"
    r"|we|us|i|they|them|he|she|you|it"
    r"|anyone|anybody|someone|somebody|everyone|everybody|nobody"
    r"|whoever|whatever|whichever|such"
    # predicate modifiers — negation, futurity/intent, disjunction (see the note
    # above: each one changes what the question asks, and a negated predicate is
    # answered with the polarity-INVERTED population)
    r"|not|never|neither|nor|longer"
    # `being` is the PROGRESSIVE and is a separate token from the infinitive
    # frame below, because no frame marker sits in front of it:
    # `is the plan being cancelled`, `is the report still being archived`.
    r"|be|being"
    # ⛔ FUTURITY/INTENT is a FRAME, not a word list: `to|going|about|due|scheduled`
    # are ordinary noun modifiers elsewhere (`is the go to market plan archived`,
    # `is the past due invoice archived`, `is the point to point integration
    # archived`) and refusing them cost that recall, so they stop the run only
    # where they introduce the predicate (`… to be retired`, `… due to be
    # cancelled`).
    r"|(?:to|going|about|due|scheduled)\s+(?:to\s+)?be\b"
    r"|or"
    # ⛔ AND AT A CLAUSE LINKER / ADJUNCT MARKER, which is the same defect one
    # grammar step further out: the ending there is inside an ADJUNCT, so the
    # question's predicate is something else entirely and the run drops it —
    # `is the data safe once archived`, `is the report still accurate once
    # archived`, and `is the policy unless bookings were cancelled` (whose own
    # finite verb `were` sat inside the run). Measured on the head that already
    # carried the negation/futurity/disjunction list: all three still routed.
    # ⛔ THE CONTRACTION IS A SUFFIX, not a token: `_NP_STOP` is a negative
    # lookahead evaluated at a TOKEN START, so a bare `n't` alternative stopped
    # only the literal word — `is the plan isn't cancelled`, `is the report wasn't
    # archived` and `is the ticket hasn't been archived` all routed and were
    # served the polarity-INVERTED population. `[a-z0-9'-]*n't` matches the token
    # that carries it.
    r"|[a-z0-9'-]*n't"
    # ⛔ THE APPROXIMATIVE NEGATIONS ARE THE SAME POLARITY CLASS — `hardly
    # archived` / `barely archived` answer the question with the population of
    # records that HAVE ended, which is the inversion the negation stop exists to
    # refuse (`is the plan hardly archived`, `is the plan barely archived` and
    # `is the plan scarcely archived` all routed on the head that stopped only the
    # literal negators; PR #14491 review, P3). They are adjectives/adverbs in the
    # PREDICATE slot and never head nouns, so they cost no recall — unlike `will`,
    # which stays a residual (`estate will probate`) rather than a stop word.
    r"|hardly|barely|scarcely|rarely"
    r"|unless|once|if|before|after|until|while|because|although|though"
    r"|as|since|whenever|whereas|whether"
    r"|each|every|both|either|another|several|many|most|few|none"
    # ⛔ AND AT AN `of`-COMPLEMENT WHOSE HEAD IS THE ENDING WORD (`is the list of
    # retired services archived`): the question is about the LIST, and the ending
    # modifies the complement, so the head noun is not the thing that ended.
    rf"|of\s+(?:{_ENDING_WORDS}))\b)"
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
#:   constraint (`what is the archived pricing document`) cannot reach it, and it
#:   refuses a WINDOW too (`what has ended in Q3`). ⛔ The vague adverbs
#:   (`recently`/`lately`) are NOT admitted — they were withdrawn in the same
#:   change that noticed the `tortoise_search` HANDLER DOCSTRING names
#:   `what has ended recently` a RELEVANCE read (§ the adverb bullet below).
#:   ⛔ IT IS THE HANDLER DOCSTRING, NOT THE SERVED DESCRIPTION (#14491 review).
#:   The served tool description is `ToolDefinition.description`
#:   (`tortoise/tool_registry.py:511-522` — its `description=` at `:514-517`) and it
#:   carries no state-routing sentence at all; the sentence that names the adverb
#:   is the handler's docstring in `tortoise/mcp_server.py`. Both cells are
#:   frozen, and the docstring is the one carrying the claim this shape defers to.
#: * ``is_predicate`` — the ending is the PREDICATE of a yes/no question about a
#:   named record: "is the onboarding flow retired?". #14033's measured miss.
#:   ⛔ Tail-anchored (the participle must END the question), the subject NP is
#:   bounded to a determiner plus at most FOUR words, and its word run STOPS at a
#:   relativiser or a clause subject, so a clause cannot smuggle its own finite
#:   verb in:
#:   `is the report archived in S3`, `is the report that legal archived` and
#:   `is the policy when a booking is cancelled` all keep the relevance read.
#:   ⛔ AND THE RESIDUAL IS THE ONE-RECORD LOOKUP ITSELF, stated rather than
#:   discovered: once the scope is derived the read DROPS the query text (it was
#:   APPLIED as the selector, not as a filter), and the ended read is a
#:   FAMILY-WIDE enumeration — the newest ``limit`` rows by ``endedAt``, where
#:   ``limit`` is the CALLER's page: ``tortoise_search``'s default is **10**
#:   (``tortoise/mcp_server.py:5634-5635``; the read's own door,
#:   ``sdk.py:_tortoise_fts_query``, defaults the same at ``:45907``) and the SDK
#:   always asks the enumerator for ``limit + 1`` (``tortoise/sdk.py:47017``) so a
#:   truncated page is PROVEN rather than guessed. (50 is ``enumerate_ended``'s
#:   own default, ``tortoise/ended.py:295-296`` — the SDK never uses it.) So the named
#:   record's ``status`` is served only when that row is on the page, and nothing
#:   in the reply separates "your record has not ended" from "your record is not
#:   on this page": a truncation marker says the population is wider, never that
#:   the named record is in it. The route is still the right one (#14033 asks for
#:   it): the answer is a state population, and the alternative is ``0 rows`` from
#:   a read that excludes terminal statuses.
#: * ``list_what`` — "list what we retired": an imperative whose `what we <verb>`
#:   CLAUSE asks for the SET. Tail-anchored, so `list what we retired last
#:   quarter` is refused rather than served with the narrowing dropped.
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
#: ⛔ #14033 WIDENED THIS SET, AND THE WIDENING IS THE POINT OF THE ISSUE. It
#: measured that the gap around the shapes was itself the defect: a question
#: that is plainly about state but is not in a shape fell through to the
#: ordinary relevance read, so the caller got a confident answer to a DIFFERENT
#: question and nothing said the state route had not engaged — silence that is
#: indistinguishable from "nothing has ended". TWO of the three measured misses
#: now have a shape below — `is the onboarding flow retired?` (``is_predicate``)
#: and `list what we retired` (``list_what``). The third,
#: `what has ended recently`, deliberately still does NOT, for the reason the
#: bullet below states. The widening stays narrow for the reason the precision
#: rounds established: each new shape fixes the GRAMMATICAL ROLE of the ending
#: word (a predicated participle ending the question, or a `what we <verb>` set
#: clause), never a word that happens to be PRESENT. #14033's four content
#: questions still do not route, and they are pinned as the falsifier in
#: ``tests/test_14033_state_cue_gap.py``:
#: `have any customers cancelled this month`, `which features are deprecated in
#: v3`, `do we archive customer emails`, `which claims were retracted by legal`.
#:
#: * "what has ended recently" / "what ended recently" — a trailing vague
#:   adverb. #14033 names it as a measured miss and it still does NOT route, on
#:   purpose: the `tortoise_search` HANDLER DOCSTRING
#:   (`tortoise/mcp_server.py`, not the served `ToolDefinition.description`)
#:   states that this exact string is a RELEVANCE read, so admitting it here
#:   would leave that documented handler contract WRONG about a phrasing it
#:   names — which is #14033's own acceptance item #3, not a way to satisfy it.
#:   Honouring it needs the docstring re-cut in the SAME change, and the docstring
#:   is a frozen baseline cell — it is part of the handler's ``co_consts``, so
#:   editing it moves ``served_from`` and the re-cut is owner-gated (#4282).
#:   The adverb would in fact be harmless for the POPULATION — the ended read is
#:   ordered by the row's own ``endedAt`` newest-first, so nothing a caller said
#:   is narrowed away — which is why this is deferred rather than refused: it is
#:   the DOCSTRING that binds, not the population.
#:   `what ended` and `what has been withdrawn` are the route until the re-cut
#:   lands.
#: * "what commitments do we have" / "what are the commitments" — a commitment
#:   noun that does not END the question or is not preceded by a set determiner;
#:   "current commitments", "our commitments" and "what are we committed to" do
#:   route.
#: * "show me the retired onboarding flow" / "list the retired onboarding flow"
#:   — a bare IMPERATIVE naming one record BY NAME, which was measured as a
#:   hijack. ⛔ `list what we retired` used to sit in this bullet and does NOT
#:   anymore (#14033): it routes through ``list_what`` below, and the
#:   discriminator is the `what we <verb>` CLAUSE — a request for the SET — not
#:   the imperative mood. The bare imperative still refuses, and the same frame
#:   with a SET noun (`list our commitments`, `show me all the commitments`) is
#:   the recall the start anchor gives up, named here so it is a decision and
#:   not a gap.
#: * "do we ever archive plans" / "do we archive customer emails" — the
#:   PRESENT-habitual frame, which `do_support` refuses on purpose (round-5
#:   review, P1): it asks what we do, not which records ended.
#: * "have any claims been retracted in Q3" / "has anything been withdrawn by
#:   legal" — an appended CONSTRAINT, refused by the tail anchor of every shape
#:   that has one, so the caller keeps the relevance read instead of having the
#:   narrowing dropped. ⛔ ``do_support`` HAS NO TAIL ANCHOR (it is prefix-anchored
#:   on `did we <verb>`, and `what did we archive last quarter` is a pinned
#:   SELECTED row), so a window there is not refused: the population is served
#:   newest-first and the caller reads each row's own ``endedAt``. That is a
#:   stated boundary, not a claim that every shape refuses a window.
#:   ⛔ AND WHERE THE SHAPE IS TAIL-ANCHORED THE TAIL ADMITS NO TOKEN AT ALL — a
#:   VAGUE ADVERB INCLUDED (`what has ended recently`, `list what we retired last
#:   quarter`). The adverb is deferred on the CONTRACT, not on precision:
#:   "recently" names no boundary, so it would narrow nothing away — but the
#:   `tortoise_search` handler docstring names that exact string a RELEVANCE
#:   read, so admitting it here needs the docstring re-cut in the SAME change
#:   (owner-gated, #4282). See the adverb bullet above.
#: * "which of our withdrawn claims affected pricing" — #14033's own fourth table
#:   row, and it KEEPS the relevance read on purpose: ``withdrawn`` there
#:   MODIFIES ``claims``, which is the same attributive slot as the pinned
#:   `which archived report has the Q3 numbers`. A modifier is not the question's
#:   predicate, and reading it as one is the round-2/4/5 hijack class. The
#:   caller reaches the population the stated way instead (``ended_scope=True``,
#:   or the unqualified `which claims were withdrawn`).
_ENDING_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "has anything we relied on been withdrawn?" / "have any claims been retired?"
    # An EXISTENCE question about an ending: the auxiliary + quantifier is the
    # subject of the question, and the relative clause in between is the thing
    # asked about. ⛔ The PARTICIPLE must then END the question, so an appended
    # constraint is refused instead of dropped (round-5 review, P1).
    ("existence", re.compile(
        rf"\b(?:has|have|had)\s+(?:any|anything|someone|something|everything|anyone)"
        rf"\b[^?]{{0,60}}?\b(?:been|already|ever)\s+(?:{_ENDING_WORDS})\b"
        rf"(?=\s*(?:[?.!]|$))")),
    # "what ended" / "what has been withdrawn" / "what was retracted" — the WHOLE
    # question, anchored at both ends, so a trailing constraint ("what is the
    # archived pricing document") and a wh-word in a subordinate clause cannot
    # reach it.
    # ⛔ AND THE TAIL ADMITS NO TRAILING TOKEN AT ALL (#14033). A vague adverb
    # would be harmless for the POPULATION — `recently` names no BOUNDARY, which
    # is the whole of the argument and not the ordering: `endedAt` is coalesced
    # over five time properties with no fallback, and FalkorDB sorts NULL FIRST
    # under that `DESC` (`analyze.py:1049-1050` records the ASC half), so a row
    # carrying none leads the page rather than trailing it — but the
    # `tortoise_search` HANDLER DOCSTRING names `what has ended recently` as a
    # RELEVANCE read, so a route here would leave that documented contract WRONG
    # about a phrasing it names. (⛔ Not the served description: that is
    # `ToolDefinition.description` in `tortoise/tool_registry.py` and it carries
    # no state-routing sentence — #14491 review.) The docstring is a frozen
    # baseline cell (its `co_consts` feed `served_from`; an owner-gated re-cut,
    # #4282), so the two move together or not at all; this shape keeps its tail
    # anchor until then (`what has ended in Q3` and `what was retired this
    # quarter` are narrowings and refuse on the same anchor).
    ("wh_whole_question", re.compile(
        rf"^what\s+(?:(?:is|are|was|were|has|have|had)\s+)?(?:been\s+)?"
        rf"(?:{_ENDING_WORDS})\s*[?.!]*$")),
    # "which claims were archived?" / "which of our endpoints is deprecated?" /
    # "which commitments were withdrawn?" — the wh-word's own predicate.
    # ⛔ THE HEAD NOUN IS ADJACENT TO THE AUXILIARY. The window used to be a free
    # 30 characters, which admits a subordinate clause: `which vendor said our
    # commitments were archived`, `which report says the product was deprecated`
    # and `which notice confirmed the account was cancelled` all routed and were
    # answered with the whole ended population. A head noun cannot be followed
    # by its own finite verb before the auxiliary, so the NP is bounded to an
    # optional partitive + determiner + ONE head noun — the same grammatical-role
    # anchor `do_support` and `committed_to` use, not a wider vocabulary.
    # The recall this gives up is stated rather than discovered: a MODIFIED head
    # noun (`which customer claims were archived`, `which older endpoints are
    # deprecated`) keeps the relevance read, because a modifier is the same slot
    # the subordinate clause occupies and the shape does not fire where it could
    # go either way.
    ("which_predicate", re.compile(
        rf"^which\s+(?:of\s+)?"
        rf"(?:(?:our|the|all|your|their|its|my|these|those)\s+)?"
        rf"[a-z]+\s+"                      # the wh-phrase's HEAD noun
        rf"(?:is|are|was|were|has|have|had)\s+"
        rf"(?:been\s+)?(?:{_ENDING_WORDS})\s*[?.!]*$")),
    # "what did we archive last quarter" / "did we retire the onboarding flow".
    # ⛔ THE BASE VERB MUST FOLLOW THE PRONOUN IMMEDIATELY. A window let a
    # subordinate clause through: `what do we do when we cancel a subscription`
    # is a how-to question, and the earlier cut served it the ended population.
    # ⛔ AND THE AUXILIARY IS `did` — the PAST form (round-5 review, P1). `do`
    # and `does` turn the same words into the present-habitual POLICY frame
    # (`do we archive customer emails`), which is a content question about what
    # we do, and every one of those was answered with the whole ended population.
    ("do_support", re.compile(
        rf"^(?:what\s+)?did\s+(?:we|i)\s+(?:{_ENDING_BASE})\b")),
    # "is the onboarding flow retired?" / "is our pricing decision withdrawn"
    # (#14033). A YES/NO question whose PREDICATE is the ending is a state
    # QUESTION — the predicate, not the mood, is what makes it one.
    # ⛔ The participle ENDS the question, which is the same tail anchor every
    # tail-anchored shape uses: `is the report archived in S3` keeps the relevance
    # read, because `in S3` is a narrowing and the ending is not the whole
    # predicate. And the subject NP is BOUNDED (a determiner plus at most FOUR
    # words) with a word run that STOPS at a relative-clause marker, so a clause
    # cannot smuggle its own finite verb in — `is the report that legal archived`,
    # `is the payment terms clause given the contract was archived` and
    # `is the policy when a booking is cancelled` all refuse. The tail anchor, the
    # NP bound, the NP stop and the start anchor are each pinned by a falsifier
    # that reaches it in `tests/test_14033_state_cue_gap.py`; the `what is …` row
    # of `NOT_SELECTED` cannot exercise any of them, because the start anchor
    # (`^(?:is|are|was|were)`) never reaches it.
    # ⛔ AND THE SUBJECT RUN STOPS AT A PREDICATE MODIFIER (PR #14491 review,
    # P1/P2): a `not`/`never` in the run INVERTS the question — `is the plan not
    # cancelled` was served the CANCELLED records — and a futurity (`to be
    # retired`) or a disjunction (`… open or cancelled`) marker asks about a state
    # the ended population cannot represent. All three classes are pinned in
    # `tests/test_14033_state_cue_gap.py`, and the affirmative temporal modifiers
    # (`already`, `still`) stay in the run so their spellings still route.
    # ⛔ THE COPULA IS THE WHOLE be-PARADIGM, not `is` alone: `was the onboarding
    # flow retired?`, `are our endpoints deprecated` and `were the claims
    # retracted` are the same question in another tense/number, and refusing them
    # costs the very fallthrough this shape exists to remove — the relevance read
    # EXCLUDES terminal statuses, so a refused state question answers `0 rows`.
    # The determiner set is the closed one (`these`/`those` included: a yes/no
    # question about ONE named record can use either), and the token class
    # survives `company's` / `all-hands` — a hyphen or an apostrophe inside a
    # head noun is an ordinary spelling, not a clause boundary.
    ("is_predicate", re.compile(
        rf"^(?:is|are|was|were)\s+"
        rf"(?:the|our|your|their|its|my|this|that|these|those)\s+"
        rf"{_NP_WORD}"                       # the question's HEAD noun
        rf"(?:\s+{_NP_STOP}{_NP_WORD}){{0,3}}"
        rf"\s+(?:{_ENDING_WORDS})\s*[?.!]*$")),

    # "list what we retired" / "list what we archived" (#14033). An IMPERATIVE,
    # which the `scoped_noun`/`show me` boundary refuses — and the discriminator
    # is the `what we <verb>` clause, not the imperative: `show me the retired
    # onboarding flow` and `list our commitments` ask for records BY NAME and
    # keep the relevance read (both are pinned), while "list WHAT we retired"
    # asks for a SET. ⛔ The participle ends the question, so an appended window
    # (`list what we retired last quarter`) is refused rather than dropped.
    ("list_what", re.compile(
        # ⛔ `withdrew` joins the alternation because this slot is the PAST
        # tense: `_ENDING_WORDS` is the PARTICIPLE family and does not contain
        # it, so `list what we withdrew` fell through to the relevance read.
        rf"^list\s+(?:out\s+)?what\s+(?:we|i)\s+(?:withdrew|{_ENDING_WORDS})"
        rf"\s*[?.!]*$")),
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


def detect_state_question(text: str | None) -> StateQuestion:
    """Map a query to the state population it asks about (:data:`POPULATIONS`).

    Deterministic and bounded — one normalisation plus a scan of nine compiled
    patterns (six ending + three commitment), no model call and no IO, so the hot
    read path pays microseconds
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
