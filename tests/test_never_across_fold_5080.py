"""D12/O4 (#5080) — a near-duplicate fold may never cross a distinguishing
difference.

The owner ruling of 2026-09-24 authorises merging near-duplicate claims and
forbids a merge across a difference in a number/quantity, a named entity, a
language, a negation, a condition, a date/scope, or a load-bearing connective
whose role changes across the pair (``#5139``).  The authoritative predicate is
``extractor_v2._boundary`` — the connective class is enumerated by
``_CONNECTIVE_SLOTS``/``_CONNECTIVE_MEMBERS``, and the labels it can report by
``distinguishing_difference``; the prose below is a reading aid.  Both
write-path fold sites enforce that boundary from ONE implementation
(``extractor_v2.fold_allowed`` / ``supersede_allowed``):

* ``classify_consolidation`` — the against-store classifier, and
* ``dedup_classify.rephrase_hit`` — the in-capture band, whose seam deletes a
  folded node outright.

A refused fold produces ADD / no hit, so both claims survive.  A differing
NUMBER or DATE is a new value for one attribute and keeps superseding (UPDATE);
a differing NEGATION, CONDITION, marker SCOPE, LANGUAGE, substituted content or
CONNECTIVE OPERATOR means the two are rival claims, which may be neither folded
nor superseded.

Every never-across pair in the tables below carries token overlap at or above
``NOOP_MIN_OVERLAP``, so the assertion is that the boundary refuses a fold the
band would otherwise have taken — not that the pair was too dissimilar to fold.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import dedup_classify
from tortoise import extractor_v2 as v2

# (prior, candidate, dimension) — one row per dimension of the ruling's
# boundary.  Each pair is a real rival claim, not a rewording of the prior.
NEVER_ACROSS = [
    ("timeout is on macOS", "timeout is not on macOS", "negation"),
    ("the plan doc is approved", "the plan doc is not approved", "negation"),
    ("we ship if the build passes", "we ship unless the build passes",
     "condition"),
    ("gym at 6pm", "gym at 5pm", "number"),
    ("daughter is 7", "daughter is 8", "number"),
    ("shipped in march", "shipped in april", "date"),
    ("the server is running", "the сервер is running", "language"),
    ("the deploy succeeded at 3pm", "the deploy failed at 3pm",
     "substituted_content"),
    ("alice best 5k is 27:12", "bob best 5k is 27:12", "substituted_content"),
    ("gym at 6pm", "yoga at 6pm", "substituted_content"),
    # A differing number BESIDE an equal clock value.  The signature is equal
    # on both sides, so a signature comparison alone returns early and folds
    # these — the value token set is what catches them.
    ("shipped 5 crates at 6pm", "shipped 3 crates at 6pm", "number"),
    ("the team meets at 9am for 3 items",
     "the team meets at 9am for 4 items", "number"),
    # Negation and condition are SCOPE-bearing: the same marker and the same
    # content tokens, attached to different operands, are a different claim.
    # A bag-of-tokens comparison reads both of these as identical.
    ("the cache is not the problem, the lock is",
     "the cache is the problem, the lock is not", "scope"),
    ("we ship if the build passes and rollback if the tests fail",
     "we ship if the tests fail and rollback if the build passes", "scope"),
    # #5139 — a load-bearing connective is FRAME by its commonest role, so a
    # swap between two operators of ONE slot left the content multiset equal
    # and the pair folded.  `and`/`or` are conjunctions (the role that keeps a
    # coordinating paraphrase foldable) and they are also operators; the same
    # holds for `then`/`else`, `than`/`as` and `to`/`from`, and for the clause
    # relations `but`/`so`/`yet`, which the issue title does not list.
    ("we ship and test", "we ship or test", "substituted_content"),
    ("we ship then test", "we ship else test", "substituted_content"),
    ("he is taller than bob", "he is taller as bob", "substituted_content"),
    ("we ship to the store", "we ship from the store",
     "substituted_content"),
    ("it rained so we stayed", "it rained and we stayed",
     "substituted_content"),
    ("we tried and failed", "we tried but failed", "substituted_content"),
    # `yet` is NOT in the frame set, so it is not dropped from the skeleton —
    # and it still folded, because the one-sided rule reads it as a detail
    # added to the prior rather than a rival operator.
    ("we tried and failed", "we tried yet failed", "substituted_content"),
    ("we shipped as the build passed", "we shipped and the build passed",
     "substituted_content"),
    # `for` is the causal conjunction here, not the preposition — the one slot
    # member whose non-operator use is common, which is why the instrumental
    # `for`/`as` rewording is a pinned residual rather than a reason to prune it.
    ("we paused for the build failed", "we paused and the build failed",
     "substituted_content"),
    # A subordinating connective is not in the frame set, so the content
    # skeleton keeps it — and the pair still folded, because a one-sided token
    # is read as a detail added to the prior (the broadening case).
    ("we paused because the build failed", "we paused and the build failed",
     "substituted_content"),
    # A SHARED slot member must not mask a swap in the same slot: both sides
    # carry `as` AND each owns a differing clause relation, so the content
    # multiset is equal and only the pair-level slot read can refuse it.
    ("we ship as planned and test", "we ship as planned or test",
     "substituted_content"),
    # `as well as` IS `and`, canonicalised to its operator, so it still rivals
    # `or` — deleting the phrase instead would have made it one-sided.
    ("we ship the server as well as the client",
     "we ship the server or the client", "substituted_content"),
    # A member is read from the TOKENS, so a combining mark that does not
    # compose with its neighbour must not hide it: the flattened read turned
    # the mark into a separator and split the member in two before `_deaccent`
    # could drop it, so the slot read empty and the pair folded.
    ("we ship a\u0338nd test", "we ship or test", "substituted_content"),
    # An entity-bearing pronoun or possessive names the subject; changing it
    # re-subjects the claim.
    ("he won the race", "she won the race", "substituted_content"),
    ("my manager approved the plan",
     "your manager approved the plan", "substituted_content"),
    # A re-subjection carried on ONE side only: a pronoun or possessive that
    # appears in just one claim changes what the claim is about, even though
    # every other one-sided token is the documented broadening case.
    ("the manager approved the plan",
     "his manager approved the plan", "substituted_content"),
    ("the plan is approved", "our plan is approved", "substituted_content"),
    # A pair of numbers is BOUND to the nouns beside them: the same two
    # numbers in a different pairing is not the same claim, and comparing the
    # numbers alone reads both pairings as one claim.
    ("we shipped 3 crates to 2 stores",
     "we shipped 2 crates to 3 stores", "number"),
    ("we have 2 cats and 3 dogs", "we have 3 cats and 2 dogs", "number"),
    ("we have 2 cats and 3 dogs", "we have 2 dogs and 3 cats", "number"),
    ("we shipped 3 crates to 2 stores",
     "we shipped 3 stores to 2 crates", "number"),
    # A clitic the hand-written list cannot enumerate: the rule is "X + n't",
    # not the ~17 verbs someone thought to write down.
    ("we mustn't ship the build", "we ship the build", "negation"),
    ("we needn't retry the deploy", "we retry the deploy", "negation"),
    ("the report is ready", "bob's report is ready",
     "substituted_content"),
    ("bob's report is ready", "alice's report is ready",
     "substituted_content"),
    # A condition the single-word list misses.
    ("we hold the release pending the tests", "we hold the release",
     "condition"),
    ("we ship iff the build passes", "we ship the build passes",
     "condition"),
    # A state pair: one side's "off" must not read as a detail added to "on".
    ("the flag is on", "the flag is off", "substituted_content"),
    ("the feature is on", "the feature is off", "substituted_content"),
    # ... and the state word on ONE side is a state DROP, not a detail added
    # (#5134): the boundary used to read every one-sided token as the
    # documented broadening, so folding "the flag is off" into "the flag"
    # destroyed the state.  The pair decides — a state word that IS the whole
    # distinguishing content of a copula predicate is a rival claim, while a
    # one-sided detail (or a prepositional `on`) stays the broadening case.
    ("the flag is off", "the flag", "substituted_content"),
    ("the feature is disabled", "the feature", "substituted_content"),
    ("the gate is open", "the gate", "substituted_content"),
    # A name on one side only re-subjects the claim, like a pronoun does.  The
    # capital letter is the signal, so a sentence-initial token does not count —
    # "Workout at the gym" is capitalised by position, not by being a name, and
    # counting it would refuse the legitimate fold pinned below.
    ("The deploy failed for Alice", "The deploy failed",
     "substituted_content"),
    ("the release was shipped to Bob", "the release was shipped",
     "substituted_content"),
    # A multi-word relative day: the shared "tomorrow" must not mask the
    # modifier that makes the two different days.
    ("the delivery is tomorrow morning",
     "the delivery is the day after tomorrow morning", "date"),
    ("we ship yesterday", "we ship the day before yesterday", "date"),
    # SCOPE with a value or a date as the marker's operand.  Dropping values
    # and dates from the scope sequence hid a marker attached to the other one.
    ("we ship today, not tomorrow", "we ship tomorrow, not today", "scope"),
    ("we ship at 6pm, not at 5pm", "we ship at 5pm, not at 6pm", "scope"),
    ("we ship today if the build passes",
     "we ship if the build passes today", "scope"),
    # A clock the value dimension can now BIND, and one the scope sequence can
    # now keep: the placeholder carries the meridiem, so 6 pm and 6 am are two
    # operands rather than one hour.  Both fire, and the value binding is what
    # the audit label reports.
    ("we meet at 6 pm, not 6 am", "we meet at 6 am, not 6 pm", "scope"),
    ("we meet at six pm, not six am", "we meet at six am, not six pm",
     "scope"),
    ("we start at 6pm and end at 9pm", "we start at 9pm and end at 6pm",
     "number"),
    ("we start at six pm and end at nine pm",
     "we start at nine pm and end at six pm", "number"),
    # ... and a DATE is bound the same way.
    ("we ship on monday and receive on tuesday",
     "we ship on tuesday and receive on monday", "date"),
    ("we ship in march and receive in april",
     "we ship in april and receive in march", "date"),
    # A clitic with the apostrophe omitted: the shape rule needs a separator,
    # so these are named in the marker list instead.
    ("we mustnt ship the build", "we ship the build", "negation"),
    ("we neednt retry the deploy", "we retry the deploy", "negation"),
    ("we hadnt shipped the build", "we shipped the build", "negation"),
    # A numeric token is its own value, separators included: canonicalising to
    # word characters alone collapsed "1.2", "50%" and "$50" onto "12" and
    # "50" and folded a different quantity into its neighbour.
    ("we shipped 1.2 tons", "we shipped 12 tons", "number"),
    ("the answer is 1.2", "the answer is 12", "number"),
    ("we got 50% of the vote", "we got 50 of the vote", "number"),
    ("we paid $50", "we paid 50", "number"),
    # Multi-word relative days are placed by POSITION, so a swap of two of them
    # is a difference — appending them in the phrase list's own order made the
    # tuple permutation-invariant and put the set back.
    ("we ship next week and receive last month",
     "we ship last month and receive next week", "date"),
    ("we ship monday and receive next week",
     "we ship next week and receive monday", "date"),
    # "may" is read as a month only in a date position, so a month swap is a
    # date difference...
    ("we ship in may and receive in june",
     "we ship in june and receive in may", "date"),
    # ... while the modal is not a date at all, and the hedge change refuses
    # both decisions through the substitution rule.
    ("we may ship friday", "we can ship friday", "substituted_content"),
    # A leading separator that begins a numeral is part of the quantity, and it
    # was being stripped before the value comparison: ".5" folded into "5".
    ("we shipped .5 tons", "we shipped 5 tons", "number"),
    # A meridiem no clock form absorbed ("6 in the am") is still a value.
    ("the meeting is at 6 in the am", "the meeting is at 6 in the pm",
     "number"),
    ("we work am shifts", "we work pm shifts", "number"),
    # An internal separator that changes a word is a content substitution, and
    # canonicalising it away collapsed the two sides onto one token.
    ("we re-sign the contract", "we resign the contract",
     "substituted_content"),
    ("we run a co-op", "we run a coop", "substituted_content"),
]

# Pairs differing in a VALUE dimension AND an identity dimension at once.  The
# label reports the IDENTITY one (it is matched first), so a caller that asked
# only for a value dimension would miss the rival identity difference; the
# decision consults the full identity set.
MASKED_IDENTITY = [
    ("the deploy succeeded at 3pm", "the deploy failed at 5pm"),
    ("the build succeeded at 3pm", "the build did not succeed at 5pm"),
    ("we retried two times and it passed",
     "we retried 3 times and it did not pass"),
    ("he won the 5k in 27:12", "she won the 5k in 25:03"),
    # A connective swap BESIDE a value difference: the number must not license
    # terminalizing the rival operator swap.
    ("we ship and test at 3pm", "we ship or test at 5pm"),
    # A dropped state word BESIDE a value difference: the number must not
    # license terminalizing the state drop as a value update.
    ("we shipped 3 crates and the flag is off", "we shipped 5 crates and the flag"),
]

# A differing value is a new value for one attribute — UPDATE is what records
# that, and the boundary must not block it.
VALUE_CHANGES = [
    ("gym at 6pm", "gym at 5pm", "number"),
    ("gym 6pm", "gym 5pm", "number"),
    ("shipped in march", "shipped in april", "date"),
]

# Folds that must keep working: a rewording, a broadening, and a notation
# change on a value.  Deleting the value-signature short-circuit to fix the
# false folds would break these.
LEGITIMATE_FOLDS = [
    ("gym at 6pm", "workout at the gym at six pm"),
    ("the team meets weekly in main office", "the team meets weekly"),
    ("gym at 6pm", "gym at six pm"),
]

# Of those, the pairs the token-overlap band actually reaches.  The first row
# folds through the classifier's value-signature path at 0.33 overlap, below
# the band — so it is deliberately absent here rather than asserted as a band
# hit it never was.
LEGITIMATE_BAND_FOLDS = [
    ("the team meets weekly in main office", "the team meets weekly"),
    ("gym at 6pm", "gym at six pm"),
]

# The dimensions where nothing may be destroyed — neither fold nor supersede.
IDENTITY_DIMENSIONS = {
    "negation", "condition", "scope", "language", "substituted_content",
    "unreadable",
}


class TestDistinguishingDifference:
    """The boundary predicate itself, dimension by dimension."""

    @pytest.mark.parametrize(("prior", "candidate", "dimension"), NEVER_ACROSS)
    def test_each_dimension_is_named(self, prior, candidate, dimension):
        assert v2.distinguishing_difference(prior, candidate) == dimension
        assert not v2.fold_allowed(prior, candidate)

    @pytest.mark.parametrize(("prior", "candidate", "dimension"), VALUE_CHANGES)
    def test_value_dimensions_still_supersede(self, prior, candidate, dimension):
        assert v2.distinguishing_difference(prior, candidate) == dimension
        assert not v2.fold_allowed(prior, candidate)
        assert v2.supersede_allowed(prior, candidate)

    @pytest.mark.parametrize(("prior", "candidate"), LEGITIMATE_FOLDS)
    def test_a_rewording_has_no_distinguishing_difference(self, prior, candidate):
        assert v2.distinguishing_difference(prior, candidate) is None
        assert v2.fold_allowed(prior, candidate)
        assert v2.supersede_allowed(prior, candidate)

    def test_clitic_negation_is_seen(self):
        """'dont' is a list entry and "don't" is not — both negate."""
        assert v2.distinguishing_difference(
            "i dont like it", "i like it") == "negation"
        assert v2.distinguishing_difference(
            "i don't like it", "i like it") == "negation"

    @pytest.mark.parametrize("apostrophe", [
        "\u2018", "\u2019", "\u201a", "\u201b", "\u00b4", "\u02bc",
        "\u02b9", "\u02bb", "\u02bc", "\u02bd", "\u02be", "\u02bf",
        "\u02c0", "\u02c1", "\u02c8", "\u02ca", "\u02cb", "\u02cc",
        "\u02d0", "\u055a", "\u05f3", "\u2032", "\u2035", "\ua78c",
        "\uff02", "\uff07"])
    def test_a_clitic_in_any_apostrophe_spelling_negates(self, apostrophe):
        """LLM output spells the clitic with every codepoint that looks like an
        apostrophe, and a negator the boundary cannot see is a negator it fails
        to guard.  The list is the whole inventory the translate table carries.
        """
        assert v2.distinguishing_difference(
            f"i don{apostrophe}t like it", "i like it") == "negation"
        assert v2.distinguishing_difference(
            f"the build isn{apostrophe}t green",
            "the build is green") == "negation"
        # ... and the spelling itself is still not a difference.
        assert v2.distinguishing_difference(
            f"i don{apostrophe}t like it", "i dont like it") is None
        # the same codepoint carries the possessive signal
        assert v2.distinguishing_difference(
            "the report is ready", f"bob{apostrophe}s report is ready"
        ) == "substituted_content"

    def test_the_apostrophe_table_is_covered_by_the_test(self):
        """The parametrisation above is the human-readable inventory; the
        translate table is the one that runs.  A codepoint in the table and not
        in the test is an unverified guarantee."""
        assert set(v2._APOSTROPHES) == {
            0x2018, 0x2019, 0x201A, 0x201B, 0x00B4, 0x02BB, 0x02BC, 0x02BD,
            0x02BE, 0x02BF, 0x02C0, 0x02C1, 0x02B9, 0x02C8, 0x02CA, 0x02CB,
            0x02CC, 0x02D0, 0x055A, 0x05F3, 0x2032, 0x2035, 0xA78C, 0xFF02,
            0xFF07,
        }

    @pytest.mark.parametrize("clitic", ["mustn't", "needn't", "shan't",
                                        "mightn't", "couldn't"])
    def test_a_clitic_is_a_negator_whatever_the_verb(self, clitic):
        """The rule is the clitic, not a hand-enumerated verb list: a fixed
        word list cannot hold every verb a negator attaches to."""
        assert v2.distinguishing_difference(
            f"we {clitic} ship the build",
            "we ship the build") == "negation"

    def test_a_notation_change_is_not_a_value_difference(self):
        """'six' and '6' are one value in two spellings."""
        assert v2.distinguishing_difference("gym at six", "gym at 6") is None

    def test_trailing_punctuation_is_not_a_substitution(self):
        """`_norm` keeps punctuation; the guard must not read 'team.' and
        'team' as two different content tokens."""
        assert v2.distinguishing_difference(
            "we ship the web server first.", "we ship the web server first"
        ) is None

    @pytest.mark.parametrize("phrase", ["as long as", "so long as", "in case",
                                        "in the event", "on condition that",
                                        "provided that", "conditional on"])
    def test_a_multi_word_condition_is_a_condition(self, phrase):
        """A condition carried by more than one word is invisible to a
        single-word marker list, and the pair then reads as one claim with a
        detail added rather than two claims with and without a condition."""
        assert v2.distinguishing_difference(
            f"we keep backups {phrase} the deploy fails",
            "we keep backups") == "condition"
        assert not v2.fold_allowed(
            f"we keep backups {phrase} the deploy fails", "we keep backups")

    def test_a_latin_difference_is_decided_by_script_not_by_content(self):
        """A script change is the one language difference decidable without a
        model; a same-script language difference is refused as substituted
        content instead, which is why the `language` label is not asserted
        for a Latin-vs-Latin pair."""
        a, b = "the final result is correct", "le r\u00e9sultat final est correct"
        assert v2.distinguishing_difference(a, b) == "substituted_content"
        assert not v2.fold_allowed(a, b)

    def test_a_one_sided_detail_is_still_a_broadening(self):
        """The allowance the boundary was built with: a token on ONE side that
        carries no entity is a claim broadened, not a rival claim.

        Pinned because the two rules above both tighten it — a rule that
        refused every one-sided token would refuse every real paraphrase.
        """
        assert v2.fold_allowed("the team meets weekly in main office",
                               "the team meets weekly")
        assert v2.fold_allowed("gym at 6pm", "workout at the gym at six pm")

    def test_a_trailing_separator_is_not_part_of_the_quantity(self):
        """A trailing full stop is punctuation, so "5." and "5" stay one value;
        only a LEADING separator can begin a numeral."""
        assert v2.distinguishing_difference("the answer is 5!",
                                            "the answer is 5") is None
        assert v2.distinguishing_difference("the answer is 5.",
                                            "the answer is 5") is None
        for lead in (".", ",", ":"):
            a, b = f"the answer is {lead}5", "the answer is 5"
            assert v2.distinguishing_difference(a, b) == "number"
            assert not v2.fold_allowed(a, b)

    def test_an_entity_fused_to_any_punctuation_is_still_an_entity(self):
        """A name is a name whatever quotes it.

        Edge punctuation is stripped in any script, because LLM output wraps a
        word in the typographic marks too.  With an ASCII-only strip, "Alice"
        inside curly quotes kept the quote, stopped reading as a proper noun,
        and the whole pair folded — the quoting character decided whether a
        named entity was visible.
        """
        for open_, close in (("\u201c", "\u201d"), ("\u00ab", "\u00bb"),
                             ("'", "'"), ("(", ")"), ("[", "]")):
            a = "The deploy failed"
            b = f"The deploy failed for {open_}Alice{close}"
            assert v2.distinguishing_difference(a, b) == "substituted_content", b
            assert not v2.fold_allowed(a, b)
        for punct in ("@", "#", "~", "/", "&", "\u00bf", "\u2026"):
            a = "The deploy failed"
            b = f"The deploy failed for {punct}Alice"
            assert v2.distinguishing_difference(a, b) == "substituted_content", b

    def test_a_pronoun_or_possessive_fused_to_punctuation_is_still_one(self):
        """The entity signal is read from the word, not from the token."""
        assert v2.distinguishing_difference(
            "the manager approved the plan",
            "\u201chis\u201d manager approved the plan") == "substituted_content"
        assert v2.distinguishing_difference(
            "the deploy failed", "bob's\u201d deploy failed") \
            == "substituted_content"
        # A quoted word is not a difference when EVERY side carries the quote.
        assert v2.distinguishing_difference(
            "\u201cthe team meets weekly\u201d", "the team meets weekly") is None
        assert v2.fold_allowed("the answer is 5!", "the answer is 5")

    def test_a_negator_fused_to_punctuation_is_still_a_negator(self):
        """Presence vs absence of a negator, whatever trails the word."""
        for tail in ("\u2026", "~~", "\u201d", "\u00bb", "", ")"):
            negated = [
                f"we do not{tail} ship the build",
                f"we ship the build{tail} not",
                f"we don't{tail} ship the build",
                "\u201cnot\u201d",
            ]
            for b in negated:
                assert v2.distinguishing_difference(
                    "we ship the build", b) == "negation", b
                assert not v2.fold_allowed("we ship the build", b)

    def test_a_condition_marker_fused_to_punctuation_is_still_one(self):
        for tail in ("\u2026", "\u201d", "\u00bb"):
            b = f"we ship the build passes if{tail}"
            assert v2.distinguishing_difference(
                "we ship the build passes", b) == "condition", b
            assert not v2.fold_allowed("we ship the build passes", b)

    def test_a_date_fused_to_punctuation_is_still_a_date(self):
        """A relative day is a date even with an ellipsis against it.

        This is NOT the word-list gap: "tomorrow" IS a date word, so the
        dimension was silenced by the trailing character, not by the list.
        """
        for b in ("we ship the build tomorrow\u2026", "we ship the build \u2026tomorrow"):
            assert v2.distinguishing_difference("we ship the build", b) == "date", b
            assert not v2.fold_allowed("we ship the build", b)

    def test_a_numeric_suffix_still_changes_what_is_counted(self):
        """Widening the strip must not re-collapse a percent or a currency."""
        assert v2.distinguishing_difference(
            "we got 50% of the vote", "we got 50 of the vote") == "number"
        assert v2.distinguishing_difference("we paid $50", "we paid 50") == "number"
        assert v2.distinguishing_difference("we run v1.2", "we run v12") == "number"
        for lead in (".", ",", ":"):
            a, b = f"the answer is {lead}5", "the answer is 5"
            assert v2.distinguishing_difference(a, b) == "number"

    def test_a_compound_number_is_one_value(self):
        """"twenty three" is 23, not the two values 20 and 3.

        Read apart, the bound pairings (boxes, 20) and (boxes, 3) matched
        a genuinely different claim spelling the same digits apart.
        """
        assert v2.distinguishing_difference(
            "we need twenty three boxes", "we need 20 3 boxes") == "number"
        assert not v2.fold_allowed("we need twenty three boxes",
                                   "we need 20 3 boxes")
        assert v2.distinguishing_difference(
            "we need twenty three boxes", "we need 24 boxes") == "number"
        # Both spellings of the SAME value stay one value.
        assert v2.distinguishing_difference(
            "we need twenty three boxes", "we need 23 boxes") is None
        assert v2.distinguishing_difference(
            "we need twenty-three boxes", "we need 23 boxes") is None
        assert v2.fold_allowed("we need forty two crates", "we need 42 crates")

    def test_an_apostrophe_collapse_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent.

        Apostrophes are removed before the content skeleton is compared, so
        that a contraction's spellings agree.  The cost is that a word needing
        its apostrophe to mean something else reaches no dimension: "we'll"
        collapses onto "well".  Distinguishing them needs a model.
        """
        assert v2.fold_allowed("we'll ship the build", "well ship the build")
        assert v2.fold_allowed("she'll ship the build", "shell ship the build")

    def test_a_script_list_gap_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5329).

        ``_SCRIPT_BLOCKS`` is finite, so a word in a script it does not name
        reaches no dimension and is a one-sided broadening.  This is the
        word-list gap's shape applied to the script list; a whole-claim
        language change is still refused as a content substitution.
        """
        assert v2.fold_allowed("we ship the build", "we ship \u0568 the build")
        assert v2.distinguishing_difference(
            "the final result is correct", "le r\u00e9sultat final est correct") \
            == "substituted_content"

    def test_a_word_list_gap_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5329).

        The marker lists are finite and a negator, condition or date word they
        do not name reaches no dimension: "hardly", "rarely", "seldom",
        "nobody", "lest", "as soon as", "tonight", "noon" all fold.  Closing
        the class needs a model, not a longer list; a longer list only moves the
        boundary of what is missed.
        """
        assert v2.fold_allowed("we hardly ship", "we ship")
        assert v2.fold_allowed("we ship lest the build fails",
                               "we ship the build fails")

    @pytest.mark.parametrize("marker", sorted(v2._NEGATION_MARKERS))
    def test_every_named_negator_negates(self, marker):
        """Class coverage, not exemplars: every member of the list, spelled the
        way the list spells it."""
        assert v2.distinguishing_difference(
            "we ship the build", f"we {marker} ship the build") == "negation"
        assert not v2.fold_allowed("we ship the build",
                                   f"we {marker} ship the build")

    @pytest.mark.parametrize("marker", sorted(v2._CONDITION_MARKERS))
    def test_every_named_condition_marker_conditions(self, marker):
        assert v2.distinguishing_difference(
            "we ship the build", f"we ship the build {marker}") == "condition"
        assert not v2.fold_allowed("we ship the build",
                                   f"we ship the build {marker}")

    @pytest.mark.parametrize("phrase", sorted(v2._CONDITION_PHRASES))
    def test_every_named_condition_phrase_conditions(self, phrase):
        assert v2.distinguishing_difference(
            "we ship the build", f"we ship the build {phrase}") == "condition"
        assert not v2.fold_allowed("we ship the build",
                                   f"we ship the build {phrase}")

    @pytest.mark.parametrize("day", sorted(v2._DATE_WORDS))
    def test_every_named_date_word_is_a_date(self, day):
        assert v2.distinguishing_difference(
            "we ship the build", f"we ship the build {day}") == "date"
        assert not v2.fold_allowed("we ship the build",
                                   f"we ship the build {day}")

    def test_a_currency_or_a_sign_is_part_of_the_quantity(self):
        """A sign or currency symbol changes what the numeral counts.

        Read from the character category, not a whitelist of ASCII characters:
        ``$``, ``+`` and ``-`` are one spelling each of two Unicode classes, and
        naming only those folded "\u00a350" into "50" and "\u00a350" into "\u20ac50"
        — two different currencies collapsing.
        """
        for symbol in ("\u00a3", "\u20ac", "\u00a5", "\u20b9", "\u20bd", "\u20a9", "\u20bf",
                       "$", "\u20b1", "\u0e3f", "\u00a2"):
            for b in ("we paid 50", f"we paid {symbol}50"):
                a = f"we paid {symbol}50"
                if a == b:
                    continue
                assert v2.distinguishing_difference(a, b) == "number", (a, b)
                assert not v2.fold_allowed(a, b)
        for sign in ("\u2212", "+", "\u00b1", "\uff0b", "\u207b"):
            a, b = f"we paid {sign}50", "we paid 50"
            assert v2.distinguishing_difference(a, b) == "number", sign
            assert not v2.fold_allowed(a, b)
        for suffix in ("\u2030", "\u2031", "\u00b0"):
            a, b = f"we got 50{suffix}", "we got 50"
            assert v2.distinguishing_difference(a, b) == "number", suffix
            assert not v2.fold_allowed(a, b)
        # A TRAILING full stop or comma is still punctuation, not a suffix.
        for tail in (".", ",", "!", "?"):
            a, b = f"the answer is 5{tail}", "the answer is 5"
            assert v2.distinguishing_difference(a, b) is None, tail

    def test_a_negator_fused_on_its_left_is_still_a_negator(self):
        """The separator can belong to the word before the negator.

        "is!not" and "do-not" are each ONE whitespace token, so an edge strip
        cannot reach a separator in the middle; canonicalised whole they read
        as "isnot"/"donot" and the negated claim folded into the positive one.
        """
        for sep in ("!", "-", ",", ":", "\u2026", "/", "~", "@", "*", "_", "+"):
            labeled = [("the build is green", f"the build is{sep}not green"),
                       ("we ship the build", f"we do{sep}not ship the build")]
            for base, b in labeled:
                assert v2.distinguishing_difference(base, b) == "negation", b
                assert not v2.fold_allowed(base, b)

    def test_an_invisible_format_character_is_not_a_word(self):
        """A zero-width character is neither whitespace nor punctuation.

        It fused a word to its neighbour: "for\u200bAlice" was ONE token that
        began lower case, so the name stopped reading as a name at all.  Format
        characters become separators before tokenising, and composition runs
        first so an accent is a spelling variant rather than a difference.
        """
        for invis in ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad"):
            for stem in ("for", "to"):
                a, b = "The deploy failed", f"The deploy failed {stem}{invis}Alice"
                assert v2.distinguishing_difference(a, b) == "substituted_content", \
                    (stem, invis)
                assert not v2.fold_allowed(a, b)
            assert v2.distinguishing_difference(
                "we ship the build", f"we do{invis}not ship the build") == "negation"
            assert v2.distinguishing_difference(
                "we ship the build passes",
                f"we ship the build passes if{invis}") == "condition"
            assert v2.distinguishing_difference(
                "we ship the build", f"we ship the build tomorrow{invis}") == "date"
        # A combining mark is a spelling variant of the same word.
        assert v2.distinguishing_difference("we ship the build",
                                            "we ship the build\u0301") is None

    def test_a_name_fused_to_the_word_before_it_is_still_a_name(self):
        """Fusion does not require a space on the LEFT of the separator.

        "for@Alice" is ONE whitespace token beginning lower case, so a
        whole-token capital test saw no name at all; the parts have to be
        tested, and the FUSED spelling registered too, because that is what the
        content skeleton holds and the one-sided test intersects against.
        """
        for sep in ("@", "#", "~", "/", "&", "\u00bf", "\u201c", "\u2026", "\u2014",
                    "|", ";", "_"):
            for stem in ("for", "to", "by"):
                a, b = "The deploy failed", f"The deploy failed {stem}{sep}Alice"
                assert v2.distinguishing_difference(a, b) == "substituted_content", \
                    (stem, sep)
                assert not v2.fold_allowed(a, b)
        # The spaced form is the same difference, and the sentence-initial
        # capital is still excluded from the name rule.
        assert v2.distinguishing_difference(
            "The deploy failed", "The deploy failed for Alice") \
            == "substituted_content"
        assert v2.fold_allowed("Workout at the gym at six pm", "gym at 6pm")

    def test_an_apostrophe_can_be_the_left_separator(self):
        """An apostrophe is a separator here, not only a clitic marker.

        Splitting on apostrophes is what finds the negator in "do'not"; a
        clitic is still caught by the whole-token shape rule, so nothing is
        lost by also reading the parts.
        """
        for sep in ("'", "\u2018", "\u2019", "\u2032", "\u2035", "\u00b4", "\u055a",
                    "\u05f3", "\uff02", "\uff07"):
            for base, b in (("we ship the build", f"we do{sep}not ship the build"),
                            ("the build is green", f"the build is{sep}not green")):
                assert v2.distinguishing_difference(base, b) == "negation", (sep, b)
                assert not v2.fold_allowed(base, b)
        # A real clitic is unaffected.
        for clitic in ("don't", "mustn't", "isn't", "won't"):
            b = f"we {clitic} ship the build"
            assert v2.distinguishing_difference(
                "we ship the build", b) == "negation", clitic

    def test_a_sign_written_apart_from_its_numeral_is_part_of_it(self):
        """The sign is a token of its own when it is written with a space.

        Removed as decoration, the numeral read as a bare value and the amounts
        folded: "we paid \u00a3 50" became "we paid 50".
        """
        for sym in ("\u00a3", "\u20ac", "$", "%", "\u2030", "\u2212", "+", "-"):
            a, b = f"we paid {sym} 50", "we paid 50"
            assert v2.distinguishing_difference(a, b) == "number", (a, b)
            assert not v2.fold_allowed(a, b)
        # A suffix reads as one only where a suffix means something (a percent
        # or a currency after the numeral).
        for sym in ("%", "\u2030", "\u00a3", "\u20ac", "$"):
            a, b = f"we got 50 {sym}", "we got 50"
            assert v2.distinguishing_difference(a, b) == "number", (a, b)
            assert not v2.fold_allowed(a, b)
        # A sentence-final full stop is still punctuation, not a sign.
        assert v2.distinguishing_difference("the answer is 5 .",
                                            "the answer is 5") is None

    def test_a_number_word_with_a_unit_folds_with_its_digits(self):
        """Two spellings of ONE quantity must not read as a value change.

        The unit branch matched digits only, so "2 hours" carried a signature
        and "two hours" carried none — identical claims reported a number
        difference and superseded one another.
        """
        for a, b in (("we wait 2 hours", "we wait two hours"),
                     ("we wait 6 hours", "we wait six hours"),
                     ("we wait 30 minutes", "we wait thirty minutes"),
                     ("we run 10km", "we run ten km"),
                     ("we run 5k", "we run five k")):
            assert v2.distinguishing_difference(a, b) is None, (a, b)
            assert v2.fold_allowed(a, b)
        # ... and a real quantity difference is still one.
        for a, b in (("we wait 2 hours", "we wait 3 hours"),
                     ("we run 5k", "we run 6k"),
                     ("we run 10km", "we run 20km")):
            assert v2.distinguishing_difference(a, b) == "number", (a, b)
            assert not v2.fold_allowed(a, b)

    def test_a_combining_mark_that_composes_is_a_known_limit(self):
        """Superseded by ``test_a_composing_mark_where_a_separator_would_be_is_a_known_limit``,
        which names the class and pins it in all three dimensions."""
        assert v2.fold_allowed("we ship the build", "we do\u0301not ship the build")

    def test_a_condition_marker_fused_on_either_side_is_still_one(self):
        """A single-word marker is read by its parts, as a negator is.

        "if!the build passes" is one whitespace token whose parts are "if" and
        "the"; matched only whole, the condition was invisible and the
        conditional claim folded into the unconditional one.
        """
        for sep in ("!", "-", ":", ",", ".", "/", "~", "@", "*", "_", "+", "#",
                    "&", "|", ";", "=", "?", "\u2026", "\u2014", "\u00b7", "\u201c",
                    "\u00bf"):
            a = "we ship the build passes"
            for b in (f"we ship if{sep}the build passes",
                      f"we ship the{sep}if build passes"):
                assert v2.distinguishing_difference(a, b) == "condition", (sep, b)
                assert not v2.fold_allowed(a, b)

    def test_a_date_word_fused_to_a_separator_is_still_a_date(self):
        """A relative day is a date word wherever the separator sits.

        The declared shape is a date word fused to a separator at EITHER edge;
        "tomorrow\u2014the launch" is one token, and reading only the whole token
        made the day invisible while the edge form was refused.
        """
        a = "we ship the launch is ready"
        for sep in ("\u2014", "!", "/", "-", "@", "#", "\u00b7", "\u2026"):
            b = f"we ship tomorrow{sep}the launch is ready"
            assert v2.distinguishing_difference(a, b) == "date", (sep, b)
            assert not v2.fold_allowed(a, b)

    def test_a_composing_mark_where_a_separator_would_be_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent.

        A combining mark standing exactly where a separator would be COMPOSES
        with the letter before it under NFC, and the two parts become one word
        with no separator left to split on: "the\u0301if" is "th\u00e9if",
        "do\u0301not" is "d\u00f3not".  A mark that does NOT compose still
        splits, because a combining mark is not a word character.  Recovering
        the two parts needs the mark to carry separator semantics, which would
        split a legitimately accented word as well ("caf\u00e9"); that is a
        modelling choice, and it is filed with the word-list gap (#5329).
        """
        for a, b in (("we ship the build", "we do\u0301not ship the build"),
                     ("we ship the build passes",
                      "we ship the\u0301if build passes"),
                     ("we ship the launch is ready",
                      "we ship tomorrow\u0301the launch is ready")):
            assert v2.fold_allowed(a, b), b
        # A mark that cannot compose keeps the split, so the same shapes are
        # refused when the mark sits against a letter with no precomposed form.
        assert v2.distinguishing_difference(
            "we ship the build passes", "we ship if\u0301the build passes") \
            == "condition"

    def test_a_composing_mark_cannot_hide_a_name_pronoun_or_date(self):
        """A mark that COMPOSES leaves the capital inside the word.

        NFC turns "for\u0301Alice" into the single word "fo\u0155Alice", so a
        capital test at a part's start saw nothing; a mark against a pronoun or
        a day word likewise stopped it matching a list that reads words
        exactly.  Every lookup now reads the de-accented form as well.
        """
        # Every lookup reads the de-accented form, so a mark against a word
        # list entry cannot hide it.
        for a, b in (("the deploy failed for\u0301Alice", "the deploy failed"),
                     ("the deploy failed to\u0301Bob", "the deploy failed"),
                     ("hi\u0301s manager approved the plan",
                      "the manager approved the plan"),
                     ("we ship urgent today\u0308", "we ship urgent")):
            assert v2.distinguishing_difference(a, b) is not None, (a, b)
            assert not v2.fold_allowed(a, b)
        # A detached possessive clitic is still that owner's possessive.
        assert v2.distinguishing_difference(
            "bob\u200b's report is ready", "the report is ready") \
            == "substituted_content"
        assert not v2.fold_allowed("bob\u200b's report is ready",
                                   "the report is ready")

    def test_a_spelled_out_minute_is_a_minute(self):
        """An unrecognised minute word must not default to :00.

        _MINUTE_WORDS names seven spellings; every other minute word defaulted
        to zero, so "six fifty pm" and "six pm" normalised to the SAME value
        and one claim deleted the other.
        """
        for b in ("we meet at six fifty pm", "we meet at six ten pm",
                  "we meet at six twenty pm", "we meet at six forty five pm"):
            a = "we meet at 6:00pm"
            assert v2.distinguishing_difference(a, b) == "number", b
            assert not v2.fold_allowed(a, b)
        # A minute that cannot be read leaves the clock un-normalised rather
        # than agreeing with every other reading of that hour.
        assert v2.distinguishing_difference(
            "we meet at six pm", "we meet at six blort pm") == "number"
        # The listed minutes, and the notation fold, are unaffected.
        assert v2.distinguishing_difference(
            "we meet at 6:00pm", "we meet at six thirty pm") == "number"
        assert v2.fold_allowed("the value is six pm", "the value is 6pm")
        assert v2.fold_allowed("gym at 6pm", "gym at six pm")

    def test_a_chain_of_leading_symbols_is_part_of_the_quantity(self):
        """Each leading symbol counts, not just the one beside the digit.

        Keeping only the innermost symbol discarded the sign, so a negative
        amount folded into a positive one.
        """
        for a, b in (("we paid \u00a350", "we paid -\u00a350"),
                     ("we paid -50", "we paid \u00a3-50"),
                     ("we paid \u00a350", "we paid \u20ac-50"),
                     ("the answer is .5", "the answer is -.5")):
            assert v2.distinguishing_difference(a, b) == "number", (a, b)
            assert not v2.fold_allowed(a, b)
        # A bracket or a quote cannot belong to a numeral, so it still comes
        # off: a parenthesised decimal is the same value.
        for a, b in (("(.5)", ".5"), ("\"50\"", "50"),
                     ("the answer is (5)", "the answer is 5")):
            assert v2.distinguishing_difference(a, b) is None, (a, b)
            assert v2.fold_allowed(a, b)

    def test_a_percent_like_sign_is_read_by_its_unicodes_name(self):
        """The percent family is a family, not a list of ASCII spellings.

        A whitelist held "%" and "\u2030" and missed the fullwidth and small
        forms, so "50\uff05" folded into "50".
        """
        for sign in ("%", "\uff05", "\ufe6a", "\u066a", "\u2030", "\u2031", "\u00b0"):
            for a, b in ((f"we got 50{sign}", "we got 50"),
                         (f"we paid {sign}50", "we paid 50")):
                # U+066A reaches the boundary as a script difference rather
                # than a number one; either way it is a difference.
                assert v2.distinguishing_difference(a, b) is not None, (a, b)
                assert not v2.fold_allowed(a, b)
        assert v2.distinguishing_difference("we got 50%", "we got 50") == "number"

    def test_a_multi_word_marker_survives_an_interior_separator(self):
        """A phrase member is matched separator-insensitively.

        The phrase table is written with plain spaces, so a substring test on
        the raw normalised text missed "as-long-as" and "next\u200bweek" — the
        member was present and the phrase was not found.
        """
        for spelling in ("as-long-as", "as_long_as", "as\u200blong as"):
            a = "we keep backups"
            b = f"we keep backups {spelling}"
            assert v2.distinguishing_difference(a, b) == "condition", spelling
            assert not v2.fold_allowed(a, b)
        for spelling in ("next-week", "last\u200bmonth", "the-day-after-next",
                         "the other-day"):
            a = "we ship"
            b = f"we ship {spelling}"
            assert v2.distinguishing_difference(a, b) == "date", spelling
            assert not v2.fold_allowed(a, b)
        # The plain spellings still refuse...
        assert v2.distinguishing_difference("we ship", "we ship next week") == "date"
        assert v2.distinguishing_difference(
            "we keep backups", "we keep backups as long as") == "condition"

    def test_a_name_fused_by_any_apostrophe_is_still_a_name(self):
        """The registered spelling must match the content skeleton's.

        The skeleton is built from the apostrophe-TRANSLATED text, so it holds
        "foralice"; read raw, the name rule registered "for\u2019alice" and the
        intersection found nothing.
        """
        for apos in ("'", "\u2018", "\u2019", "\u2032", "\u2035", "\u00b4",
                     "\u055a", "\u05f3", "\uff02", "\uff07"):
            b = f"The deploy failed for{apos}Alice"
            assert v2.distinguishing_difference("The deploy failed", b) \
                == "substituted_content", apos
            assert not v2.fold_allowed("The deploy failed", b)

    def test_a_non_composing_mark_in_a_possessive_is_still_a_possessive(self):
        """A mark that does NOT compose keeps the split, and stays in scope."""
        for mark in ("\u0301", "\u0308", "\u0300"):
            b = f"bob{mark}s report is ready"
            assert v2.distinguishing_difference(
                "the report is ready", b) == "substituted_content", mark
            assert not v2.fold_allowed("the report is ready", b)

    def test_a_range_or_a_version_is_a_different_quantity(self):
        """The separators inside a numeric token are part of its value."""
        for a, b in (("we ship 3-4 crates", "we ship 4-3 crates"),
                     ("we ship 3-4 crates", "we ship 3 4 crates"),
                     ("we run v1.2.3", "we run v1.2.4"),
                     ("we run v1.2.3", "we run v123"),
                     ("we ship 1.2 tons", "we ship 12 tons"),
                     ("we ship 1.2 tons", "we ship 1.3 tons")):
            assert v2.distinguishing_difference(a, b) == "number", (a, b)
            assert not v2.fold_allowed(a, b)

    def test_an_ambiguous_month_is_a_date_only_in_a_date_position(self):
        """"may" is a month and the commonest modal, and neither blanket rule is
        safe: as a date word everywhere it made a hedge a DATE change (a value
        dimension, so it terminalised the claim); nowhere it lost a real month
        difference.  It counts after a date preposition only.
        """
        assert v2.distinguishing_difference("we ship in may",
                                            "we ship in june") == "date"
        label = v2.distinguishing_difference("we may ship friday",
                                             "we can ship friday")
        assert label != "date"
        assert not v2.supersede_allowed("we may ship friday",
                                        "we can ship friday")

    def test_a_coordinating_connective_still_folds(self):
        """The FRAME use of a connective is still frame — the PAIR decides.

        The role is read from the pair, not from the token list, so a frame
        connective is refused only where it did OPERATOR work (#5139).  Three
        shapes of legitimate fold must survive:

        * ONE side only — a comma list owns no `and`, so a single side fills
          the slot and the connective did no work;
        * a SYNONYM in one slot — `with`/`by` assert the same relation, so
          they are deliberately not one family and the rewording folds;
        * a DIFFERENT slot — the phase-D seam's own restatement swaps `to`
          (transfer direction) for `and` (clause relation), which is a
          rewording, not a swap of one slot's member.
        """
        for both, comma in (
                ("we ship the server and the client",
                 "we ship the server, the client"),
                ("we ship the server or the client",
                 "we ship the server, the client")):
            assert v2.distinguishing_difference(both, comma) is None, both
            assert v2.fold_allowed(both, comma)
        # `and then` and `, then` are one claim: `and` and `then` sit in
        # DIFFERENT slots, so the `and` side adds a member without rivaling
        # `then` — the reason the two are not one family.
        assert v2.fold_allowed("we ship the build and then test",
                               "we ship the build, then test")
        # A multi-word COORDINATION is canonicalised to its operator: `as well
        # as` is `and`, so it folds against both the `and` and the comma form,
        # and still rivals `or` (pinned in the table above).
        assert v2.fold_allowed("we ship the server as well as the client",
                               "we ship the server and the client")
        assert v2.fold_allowed("we ship the server as well as the client",
                               "we ship the server, the client")
        # ... and a one-sided mate INSIDE a shared slot is the broadening case
        # again, not a swap: both sides carry `for`, only one carries `and`.
        assert v2.fold_allowed("we wait for the build and the tests",
                               "we wait for the build, the tests")
        # ... and a slot member on ONE side only, so the commonest spelling of
        # `for` stays foldable.
        assert v2.fold_allowed("we ship the build for the client",
                               "we ship the build")
        assert v2.fold_allowed("we ship with the courier",
                               "we ship by the courier")
        # A coordination phrase is matched on a phrase EDGE that is "not
        # alphanumeric", so a word that merely ENDS in "as" before " well as"
        # is not rewritten into `and`...
        assert v2.fold_allowed("it was well as expected",
                               "it was as expected")
        assert v2.fold_allowed("the gas well as a fuel is fine",
                               "the gas as a fuel is fine")
        assert not v2.distinguishing_difference("the gas well as a fuel",
                                                "the gas as a fuel")
        # ... and the canonicalisation is spelling-INSENSITIVE, because the
        # member pass below it is: a capital (sentence-initial or mid-sentence)
        # and an underscore emphasis must read as the same phrase, and the
        # comparison slot must be left EMPTY rather than holding a leaked `as`.
        # The underscore miss was the old `\b` edge (`_` is a word character to
        # `re`); the capital miss was the raw-text read.  Both are pinned here,
        # in BOTH directions: the phrase is one operator, so it must fold
        # against `and` AND still rival another operator.
        for spelled in ("We ship the server As well as the client",
                        "We ship the server _as well as_ the client",
                        "As well as the client, we ship the server"):
            assert v2._connective_slots(spelled)[0] == frozenset({"and"}), \
                spelled
            assert v2._connective_slots(spelled)[2] == frozenset(), spelled
            assert v2.fold_allowed(spelled,
                                   "We ship the server and the client.")
            assert not v2.fold_allowed(
                spelled, "We ship the server as well, or the client")
        assert v2.fold_allowed(
            "We ship the server _as well as_ the client",
            "We ship the server, the client")
        assert v2.fold_allowed(
            "We should ship the web server first to unblock the mobile team.",
            "We should ship the web server first and unblock the mobile "
            "team now.")

    def test_a_temporal_order_swap_is_caught_by_the_content_rule(self):
        """`before`/`after` are NOT slot members — and need not be.

        Neither is a frame word, so each side keeps its own ordering token and
        the two-sided content-substitution rule already refuses the swap.  A
        slot entry would be dead weight, which is why the table excludes them.
        """
        assert v2.distinguishing_difference(
            "we ship before the tests pass",
            "we ship after the tests pass") == "substituted_content"
        assert not v2.fold_allowed("we ship before the tests pass",
                                   "we ship after the tests pass")
        assert "before" not in v2._CONNECTIVE_MEMBERS
        assert "after" not in v2._CONNECTIVE_MEMBERS

    def test_a_shared_member_cannot_mask_a_swap_in_the_same_slot(self):
        """Both sides owning a differing member is a swap, shared member or not.

        The one-sided reading is the broadening case; the TWO-sided reading is
        not.  A slot that shares a member (`as` fills two slots, so it can be
        shared) must not hide a differing extra on each side — otherwise the
        guard fails OPEN on exactly the class it exists to close.
        """
        for prior, candidate in (
                ("we ship as planned and test", "we ship as planned or test"),
                ("we ship and test as agreed", "we ship or test as agreed"),
                ("we ship and test but wait", "we ship and test so wait")):
            assert v2._connective_swap(prior, candidate), (prior, candidate)
            assert v2.distinguishing_difference(prior, candidate) \
                == "substituted_content", (prior, candidate)
            assert not v2.fold_allowed(prior, candidate)
            assert not v2.supersede_allowed(prior, candidate)

    def test_a_marked_member_spelling_does_not_hide_the_swap(self):
        """A member carrying a non-composing mark is still that member.

        `_deaccent` exists for exactly this, and it can only run on a token that
        is still ONE token: the flattened read turned the mark into a separator
        and split the member first, so the slot read empty and the pair folded.
        The same reason forbids de-accenting the WHOLE claim, which would glue a
        mark-SEPARATED member to its neighbour — so both spellings are pinned.

        A pin that cannot fail on the old read is not coverage.
        """
        assert v2._connective_slots("we ship a\u0338nd test")[0] == \
            frozenset({"and"})
        assert v2._connective_slots("we ship o\u0338r test")[0] == \
            frozenset({"or"})
        # The mark as a SEPARATOR: the member is glued to the next word, and it
        # must still be read as the member rather than fused into one token.
        assert v2._connective_slots("we ship the server and\u0338the client") \
            == (frozenset({"and"}), frozenset(), frozenset(), frozenset())
        # The mark INSIDE the phrase: still the coordination, not a stray `as`.
        assert v2._connective_slots("we ship as\u0338well as the client") == \
            (frozenset({"and"}), frozenset(), frozenset(), frozenset())
        # A mark or a diacritic inside a phrase WORD, too.  The phrase's `as`
        # is a standalone token here, so left uncanonicalised it fills the
        # clause AND comparison slots and MASKS the operator swap the phrase
        # stands for — `as well as` (an `and`) folds into a bare comparison
        # `as`, which is the fail-open class this boundary exists to close.
        for spelled in ("as we\u0338ll as", "as w\u00e9ll as",
                        # BOTH at once: a mark where the separator is AND a
                        # diacritic in the word.  A pass that only deletes
                        # marks reads these as "aswell as" and finds no
                        # separator; a pass that only keeps them misses the
                        # diacritic.  Only the two read together find them.
                        "as\u0338w\u00e9ll as", "as\u0338we\u0338ll as"):
            phrase = f"we ship the server {spelled} the client"
            assert v2._connective_slots(phrase) == (frozenset({"and"}),
                                                    frozenset(), frozenset(),
                                                    frozenset()), spelled
            assert v2._connective_swap(phrase,
                                       "we ship the server as the client"), \
                spelled
            assert v2.distinguishing_difference(
                phrase, "we ship the server as the client") \
                == "substituted_content", spelled
            assert not v2.fold_allowed(phrase,
                                       "we ship the server as the client"), \
                spelled
        for prior, candidate in (("we ship a\u0338nd test", "we ship or test"),
                                 ("we ship and test", "we ship o\u0338r test"),
                                 ("we ship the server and\u0338the client",
                                  "we ship the server or the client"),
                                 ("we ship as we\u0338ll as the client",
                                  "we ship as the client"),
                                 ("we ship as w\u00e9ll as the client",
                                  "we ship as the client"),
                                 ("we ship as\u0338w\u00e9ll as the client",
                                  "we ship as the client"),
                                 ("we ship as\u0338we\u0338ll as the client",
                                  "we ship as the client")):
            assert v2._connective_swap(prior, candidate), (prior, candidate)
            assert v2.distinguishing_difference(prior, candidate) \
                == "substituted_content", (prior, candidate)
            assert not v2.fold_allowed(prior, candidate)
            assert not v2.supersede_allowed(prior, candidate)

    def test_a_contraction_does_not_spell_a_phrase_word(self):
        """The phrase's inner gap is the one a dropped MARK leaves (#5139).

        A non-word interior would let a real token stand inside a phrase word:
        `we'll` would spell `well`, so `as we'll, as` would canonicalise to
        `and` — DELETING the comparison operators the swap guard needs to see,
        which folds a comparison into a coordination (the claim-loss fail-open
        this boundary exists to close, reached mark-free).  The gap therefore
        admits only the sentinel a dropped mark produces.  Pinned beside the
        mark spellings, because the two must hold at once.
        """
        for contraction in ("we ship the server as we'll, as the plan unfolds",
                            "we ship the server as we'll as it goes"):
            assert v2._connective_slots(contraction)[0] == frozenset({"as"}), \
                contraction
            assert not v2.fold_allowed(
                contraction, "we ship the server and the plan unfolds"), \
                contraction
        for spelled in ("as\u0338w\u00e9ll as", "as\u0338we\u0338ll as"):
            phrase = f"we ship the server {spelled} the client"
            assert v2._connective_slots(phrase)[0] == frozenset({"and"}), \
                spelled
        # The gap's sentinel must ALSO be unproducible from the claim itself: a
        # literal NUL is a character content can contain (a PDF or a JSON
        # escape), and if it reached the phrase's word gap it would spell the
        # same false `and` a contraction does.
        for injected in ("we ship the server as we\x00ll, as the plan unfolds",
                         "we ship the server as we\x00ll as it goes"):
            assert v2._connective_slots(injected)[0] == frozenset({"as"}), \
                injected
            assert not v2.fold_allowed(
                injected, "we ship the server and the plan unfolds"), injected

    def test_a_connective_swap_between_two_slots_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5325).

        The slot is the FAMILY, not the POSITION: comparing position in the
        connective sequence would refuse the phase-D restatement above, whose
        `to` and `and` sit in the same position in two different slots.  The
        cost is that a swap BETWEEN slots is invisible — `and` (clause
        relation) against `then` (branch/sequence) asserts a different
        relation and still folds.  Grouping them would refuse `and then` /
        `, then`, which is a real paraphrase.
        """
        assert v2.fold_allowed("we ship and test", "we ship then test")

    def test_a_connective_permutation_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5325).

        Within a slot the comparison is a SET, because a set is what keeps the
        documented broadening case folding.  The cost is that a change of
        ATTACHMENT carries one multiset in two groupings and folds — the same
        shape as the role inversion (#5131), which needs syntax.
        """
        assert v2.fold_allowed("we ship and test or we wait",
                               "we ship or test and we wait")

    def test_a_one_sided_extra_operator_in_a_shared_slot_is_a_known_limit(
            self):
        """Documented residual, pinned so it cannot go silent (#5325).

        A slot with a SHARED member and a member on ONE side only is read as
        the documented broadening case — the rule that keeps the comma-list
        coordination folding.  The cost is that a genuinely added operator
        clause ("but we wait") is read as an added detail.  Closing it needs
        syntax, and the shared-member rule cannot be tightened without
        refusing "we wait for the build and the tests" ⇄ "… for the build, the
        tests".
        """
        assert v2.fold_allowed("we ship and test",
                               "we ship and test but we wait")

    def test_a_within_relation_synonym_swap_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5325).

        The clause-relation slot is deliberately COARSE: splitting it into
        relation sub-families would make `and` against `but` a cross-slot
        difference and fold it — reopening the class this table exists to
        close.  The cost is that two members that happen to be near-synonyms
        are refused.  A wrong keep is noise; a wrong drop is memory loss.
        """
        for member, mate in (("but", "yet"), ("because", "as")):
            assert not v2.fold_allowed(f"we tried {member} failed",
                                       f"we tried {mate} failed")

    def test_an_instrumental_for_as_rewording_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5325).

        `for` is the one slot member whose NON-operator use is common: as a
        causal conjunction it rivals `and`, and as a preposition it does not.
        Reading it as a slot member therefore refuses the instrumental
        `for`/`as` rewording, and the refusal is KEPT deliberately — the
        boundary's own asymmetry is that a wrong keep is noise while a wrong
        drop is memory loss, so pruning `for` out would reopen the class this
        table exists to close.
        """
        assert not v2.fold_allowed("we use the tool for a hammer",
                                   "we use the tool as a hammer")

    def test_the_connective_slots_are_pinned_against_the_sibling_tables(self):
        """One token's role is declared in three sets; the overlaps are pinned.

        `and` is a frame word AND a clause-relation operator, and `nor` is also
        a negator — three answers to three different questions (syntactically
        inert? load-bearing relation operator? negation marker?) which the
        module keeps separate on purpose.  Unpinned, an edit to either sibling
        set silently changes what the boundary can see; `yet` is the worked
        example, in no sibling list at all.
        """
        assert {
            "and", "or", "but", "so", "as", "for", "then", "else",
            "than", "to", "from"} == v2._FRAME_STOPWORDS & v2._CONNECTIVE_MEMBERS
        assert {"nor"} == v2._NEGATION_MARKERS & v2._CONNECTIVE_MEMBERS
        # The condition vocabulary is empty against the slots today; pinned so
        # a next lane adding a temporal/causal word to one and not the other is
        # not silent (`when`/`while`/`once` are condition markers, `since` is
        # neither, and `before`/`after` are ordinary content tokens).
        assert frozenset() == v2._CONDITION_MARKERS & v2._CONNECTIVE_MEMBERS
        assert "yet" not in v2._FRAME_STOPWORDS
        assert "yet" not in v2._NEGATION_MARKERS

    def test_the_slot_partition_is_pinned_literally(self):
        """Which member sits in WHICH slot is pinned, not just the union.

        A behavioural test alone cannot pin the partition, and the per-member
        test below cannot either: it reads the same module constant the code
        reads, so a mis-assignment is invisible to it.  Only a literal pin makes
        the partition checkable.  `as` is the deliberate dual member.
        """
        assert tuple(tuple(sorted(slot))
                     for slot in v2._CONNECTIVE_SLOTS) == (
            ("although", "and", "as", "because", "but", "for", "nor",
             "or", "so", "though", "whereas", "yet"),
            ("else", "then"),
            ("as", "than"),
            ("from", "to"))
        # The union is what the member lookup reads, so a member added to it
        # but to NO slot would be invisible to `_connective_swap` and to the
        # table pin above.
        assert frozenset().union(*v2._CONNECTIVE_SLOTS) == \
            v2._CONNECTIVE_MEMBERS
        assert [i for i, slot in enumerate(v2._CONNECTIVE_SLOTS)
                if "as" in slot] == [0, 2]
        assert v2._COORDINATION_PHRASES == (("as well as", "and"),)

    @pytest.mark.parametrize("slot", v2._CONNECTIVE_SLOTS,
                             ids=lambda s: "+".join(sorted(s)))
    def test_every_slot_member_is_exercised_against_a_slot_mate(self, slot):
        """Class coverage, not exemplars: every member of every slot, spelled
        the way the table spells it, and ATTRIBUTED to the slot predicate.

        The mate is a FRAME-word member of the same slot wherever one OTHER
        than the member exists, so a frame member's pair has no content token on
        either side that the other lacks and no other dimension can refuse it —
        only the slot read can.  The slot read is asserted directly as well as
        through the label, because `nor` is also a negator and refuses the pair
        on that ground whatever the slot read says.  The label is asserted
        POSITIVELY: `unreadable` is the fail-closed sentinel, and an assertion
        that merely admits "some identity dimension" passes when the comparison
        crashes instead of detecting the swap.
        """
        frame = sorted(slot & v2._FRAME_STOPWORDS)
        for member in sorted(slot):
            mates = [f for f in frame if f != member] or sorted(
                slot - {member})
            assert mates, (member, slot)
            mate = mates[0]
            a, b = f"we ship {member} test", f"we ship {mate} test"
            assert v2._connective_swap(a, b), (member, mate)
            label = v2.distinguishing_difference(a, b)
            assert label in {"substituted_content", "negation"}, \
                (member, mate, label)
            assert not v2.fold_allowed(a, b)

    def test_a_sentence_initial_name_is_a_known_limit(self):
        """Documented residual, pinned with the uncapitalised name (#5134).

        A name is caught by its capital, and a sentence-initial capital is
        positional: treating it as name-shaped would refuse `Workout at the gym
        at six pm` / `gym at 6pm`, whose first token is capitalised for the same
        reason.
        """
        assert v2.fold_allowed("the plan was approved", "Alice approved the plan")

    def test_a_lowercase_name_on_one_side_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5134).

        A name is caught by its capital letter (see the NEVER_ACROSS rows), and
        a name the caller wrote in lower case looks exactly like a detail
        token.  Reading it as an entity needs NER: no shape rule can separate
        "alice" in "the deploy failed for alice" from "office" in "the team
        meets in main office", and the second is the broadening case that must
        keep folding.
        """
        assert v2.fold_allowed("the deploy failed for alice",
                               "the deploy failed")
        assert v2.fold_allowed("the team meets weekly in main office",
                               "the team meets weekly")

    def test_a_one_sided_state_word_is_a_rival_claim(self):
        """The one-sided state drop is refused — the limit is CLOSED (#5134).

        This replaces `test_a_one_sided_antonym_is_a_known_limit`, which
        pinned the fold.  A state word on one side used to read as the
        documented broadening case, so `the flag is off` folded into `the
        flag` and the in-capture seam then `DETACH DELETE`d the rival.  A
        polarity pass over the PAIR now refuses it: the state term is the
        whole distinguishing content of a copula predicate, so dropping it
        substitutes the claim instead of broadening it.  Both decisions are
        asserted, because the boundary guards fold AND supersede from one
        identity set — a fold that emptied the state out as a value update
        would destroy the same claim.  The final row pins the
        DISTINGUISHING-member read: a state that the two sides SHARE must not
        mask a one-sided extra beside it.
        """
        for prior, candidate in (
                ("the flag is off", "the flag"),
                ("the flag is on", "the flag"),
                ("the feature is disabled", "the feature"),
                ("the feature is enabled", "the feature"),
                ("the gate is open", "the gate"),
                ("the gate is closed", "the gate"),
                ("the account is locked", "the account"),
                ("the service is available", "the service"),
                # `am` is a `_CLOCK_UNITS` member, so the content skeleton
                # drops it and the copula has to be declared in `_COPULAS`;
                # without that this pair folded.
                ("i am offline", "i am"),
                # A one-sided state beside a SHARED state: the gate's `off`
                # also appears on the flag, so subtracting every polarity
                # member from one side would fail the equality and miss it.
                ("the flag is off and the gate is on",
                 "the flag is off and the gate is off")):
            assert v2.distinguishing_difference(prior, candidate) \
                == "substituted_content", (prior, candidate)
            assert not v2.fold_allowed(prior, candidate), (prior, candidate)
            assert not v2.supersede_allowed(prior, candidate), (prior, candidate)
            # The drop reads the same pair either way round.
            assert not v2.fold_allowed(candidate, prior), (candidate, prior)
        # EVERY declared member must act as a one-sided drop, not only the
        # rows above: a member that is also a frame/date stopword would be
        # silently inert in the content skeleton, and the literal pin alone
        # would not catch it.
        for member in sorted(v2._POLARITY_MEMBERS):
            assert not v2.fold_allowed(f"the thing is {member}", "the thing"), \
                member
        # The copulas the predicate read consults are a declared set too: a
        # member dropped from it silently reopens the fold for that spelling.
        # The set is pinned LITERALLY and the loop iterates a LITERAL sequence
        # — iterating `v2._COPULAS` would simply skip a removed member, which
        # is the vacuity this replaced.  `substituted_content` (not merely a
        # refusal) is asserted so an accidental refusal through another
        # dimension cannot mask a broken copula path.
        assert frozenset({
            "am", "is", "are", "was", "were", "be", "been", "being"}) \
            == v2._COPULAS
        for copula in ("am", "is", "are", "was", "were", "be", "been",
                       "being"):
            assert v2.distinguishing_difference(
                f"the thing {copula} off", "the thing") \
                == "substituted_content", copula

    def test_a_one_sided_detail_is_still_the_documented_broadening(self):
        """The one-sided allowance is load-bearing and must keep folding.

        The state pass must not tighten the general rule: a one-sided token
        that does NOT complete a copula predicate is still a detail added to
        the prior, which the boundary deliberately allows.  The `#4652`
        marker-free paraphrase itself is pinned by
        `test_a_one_sided_detail_is_still_a_broadening` and by
        `test_the_legitimate_folds_still_fold`; this test pins the
        PREPOSITIONAL `on` boundary the state pass could plausibly break — an
        `on` whose object is a token the content skeleton drops (`on friday`)
        or keeps (`on quality`), which looks exactly like a one-sided state
        word until the PAIR is read.
        """
        for detail, bare in (
                ("we ship on friday", "we ship friday"),
                ("the focus is on quality", "the focus is quality"),
                # A copula + prepositional `on` whose OBJECT is a date token.
                # The content skeleton drops the date, so the pair looks like a
                # state drop unless the predicate read keeps the date as a
                # blocker (`_TAIL_IGNORABLE`).  `today`/`yesterday` are the
                # load-bearing case: they are ALSO frame stopwords, so only the
                # subtraction keeps them blocking.
                ("the release is on friday", "the release is friday"),
                ("the release is on today", "the release is today"),
                ("the demo is on tomorrow", "the demo is tomorrow"),
                ("timeout is on macOS", "timeout is macOS"),
                # A shared trailing state must not license refusing a dropped
                # PREPOSITIONAL `on`: the qualifying copula here belongs to
                # the trailing clause both sides carry, not to the dropped
                # token.  Pinned because the pre-`targets` read refused it.
                ("we ship on friday and the flag is off",
                 "we ship friday and the flag is off")):
            assert v2.distinguishing_difference(detail, bare) is None, \
                (detail, bare)
            assert v2.fold_allowed(detail, bare), (detail, bare)

    def test_the_polarity_vocabulary_is_pinned_literally(self):
        """The declared state vocabulary is pinned, not just exercised.

        A behavioural test alone cannot pin the vocabulary: a member dropped
        from the set changes recall silently, and a member the table does not
        contain is exactly the residual class.  The pair structure is a flat
        set by design (a DROP has no second side to match a slot against), so
        the set is what is pinned.  `on` is the member with a common NON-state
        use, and it was SUBTRACTED from `_CONTENT_STOPWORDS`: it IS a frame
        stopword by its preposition use, and the STATE use is why it is carved
        out to content (so the state pair is not lopsided).  Both facts are
        pinned here so an edit to either side is not silent.
        """
        assert frozenset({
            "on", "off", "enabled", "disabled", "open", "closed",
            "active", "inactive", "available", "unavailable",
            "locked", "unlocked", "muted", "unmuted", "online",
            "offline", "valid", "invalid", "present", "absent"
        }) == v2._POLARITY_MEMBERS
        # `on` names a STATE, so it is content (the recorded decision the
        # symmetric `on`/`off` row above rests on); `off` was never a frame
        # word.  Either moving silently would make a state pair lopsided.
        assert "on" not in v2._CONTENT_STOPWORDS
        assert "off" not in v2._FRAME_STOPWORDS
        # The vocabulary and the content skeleton are COUPLED: the predicate
        # reads members out of `_content_tokens`, which drops every
        # `_CONTENT_STOPWORDS`/`_DATE_WORDS` token.  A member added to the
        # vocabulary that is ALSO a stopword or a date word would be silently
        # dead — the literal pin would force a set edit, but nothing would
        # exercise it.  `in` is the live trap (a natural state word and a
        # frame stopword), so the invariant is asserted generally, not only
        # for `on`.
        assert not (v2._POLARITY_MEMBERS & v2._CONTENT_STOPWORDS)
        assert not (v2._POLARITY_MEMBERS & v2._DATE_WORDS)
        # The ignorable tail set is where the `on`-preposition distinction
        # lives.  Only the date words that are ALSO frame stopwords are
        # load-bearing there (`today`/`yesterday`): as frame they would be
        # ignored in the tail, so `the release is on today` would read as the
        # state `on` with an empty complement and a legitimate fold would be
        # refused.  A non-frame date (`friday`) blocks the tail on its own, so
        # pinning `friday` would be vacuous — it is pinned as such instead.
        assert "today" not in v2._TAIL_IGNORABLE
        assert "yesterday" not in v2._TAIL_IGNORABLE
        assert "friday" in v2._DATE_WORDS
        assert "friday" not in v2._CONTENT_STOPWORDS
        assert "the" in v2._TAIL_IGNORABLE

    def test_a_state_word_outside_the_polarity_table_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5134).

        The vocabulary is finite, so a state word it does not name reaches no
        dimension and the pair is the one-sided broadening case again.  `shut`
        and `down` are the worked examples: both are state words and neither is
        in the vocabulary.  The asymmetry is the point — `closed`/`open` ARE
        named, so `the door is closed` is refused while `the door is shut` is
        not, and adding every English adjective is a lexicon problem rather
        than a boundary rule.  The contrast is asserted beside the pin, so this
        records the boundary and not a predicate that never fires.
        """
        assert not v2.fold_allowed("the door is closed", "the door")
        assert v2.fold_allowed("the door is shut", "the door")
        assert v2.fold_allowed("the server is down", "the server")

    def test_a_non_be_state_predicate_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5134).

        The predicate read names the be-copulas.  A non-be linking verb
        (`seems`, `remains`) is a content token, so the two sides stop being
        one claim minus a state and the pair folds.  This is the same
        open-class residual the vocabulary pin above records: folding is the
        FAIL-OPEN direction (one capture order can still drop the state-bearing
        claim), not a wrong keep.  The be-copula contrast is asserted so the
        pin cannot go vacuous.
        """
        assert not v2.fold_allowed("the flag is off", "the flag")
        assert v2.fold_allowed("the flag seems off", "the flag")
        assert v2.fold_allowed("the flag remains off", "the flag")

    def test_a_predicate_with_a_second_content_token_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5134).

        The pass reads the state term as the ENTIRE predicate complement of a
        copula.  A second content token in that complement — a passive/particle
        form (`was turned off`), an adverb beside it (`currently off`), or a
        following modifier (`off by default`, `off again`) — makes the state
        no longer the WHOLE of what is dropped, so the pair folds.

        This is the SAME condition that keeps the prepositional `on` folding
        (its object is a second content token), so the cost is deliberate: the
        refusal would otherwise have to fire on any one-sided member, which is
        the over-block the copula condition exists to avoid.  Closing it needs
        syntax.  Both directions are asserted — the bare state drop is still
        refused — so this pin cannot go vacuous by a rule that stops firing.
        """
        assert not v2.fold_allowed("the flag is off", "the flag")
        for a, b in (
                ("the switch was turned off", "the switch"),
                ("the flag is currently off", "the flag"),
                ("the flag is off by default", "the flag"),
                ("the flag is off again", "the flag")):
            assert v2.fold_allowed(a, b), (a, b)

    def test_a_state_drop_beside_another_subject_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5134).

        A claim holding a SECOND subject is not the one-sided case in either
        direction: the removed text is a whole clause rather than one state, so
        `content_a - polarity` is not the other side's skeleton.  The boundary's
        one-sided allowance therefore reads it as broadening.  Closing it needs
        to know which clause was dropped, which is syntax.
        """
        for a, b in (
                ("the flag is off and the gate is closed", "the flag is off"),
                # ... and the mirror: the FIRST state is the one dropped here.
                ("the flag is off and the gate is closed",
                 "the gate is closed")):
            assert v2.fold_allowed(a, b), (a, b)
        # The bare one-state drop is refused beside it, so the pin records the
        # boundary rather than a predicate that never fires.
        assert not v2.fold_allowed("the flag is off", "the flag")

    def test_an_inverted_or_fused_state_predicate_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5134).

        `_state_predicate` reads a copula and then its complement, in order,
        so two spellings put the state outside that read and the pair folds
        again:

        * a FRONTED copula — `is the flag off` carries the subject between the
          copula and the state, so the complement is not the state alone.
        * a state member FUSED to a separator — `on\u0338off` is one token, and
          `_deaccent` collapses the mark, so no member is read.  That is the
          same fused-token route PR #5320 (open, #5139) closes for the
          load-bearing connective's own members; here it is not closed because
          `_content_tokens` is a set and splits nothing, so closing it would
          change what the skeleton IS for every dimension, not just this one.

        Both are FAIL-OPEN residuals — the pair folds, so one capture order can
        still drop the state-bearing claim.  Each pin asserts the declarative
        drop is still refused beside it, so the pin records a precise boundary
        rather than a predicate that never fires.
        """
        assert not v2.fold_allowed("the flag is off", "the flag")
        assert v2.fold_allowed("is the flag off", "the flag")
        assert v2.fold_allowed("the flag is on\u0338off", "the flag")

    def test_a_state_drop_in_a_compound_clause_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5134).

        `_state_predicate` reads a copula's complement to the END of the token
        sequence, so content in a FOLLOWING clause disqualifies an otherwise
        bare one-sided state:

            "the flag is off and the deploy failed"
              vs "the flag and the deploy failed"       -> the state is dropped

        The mirror puts the state in the final clause and IS refused, so the
        guard is clause-ORDER dependent today.  Closing it means bounding the
        complement at a clause boundary — a connective-role decision that
        belongs with PR #5320 (#5139), not a polarity member; doing it here
        would duplicate that mechanism.  Filed separately.  The refused mirror
        is asserted beside the pin, so the pin cannot go vacuous.
        """
        assert not v2.fold_allowed("the build passed and the flag is off",
                                   "the build passed and the flag")
        for a, b in (
                ("the flag is off and the deploy failed",
                 "the flag and the deploy failed"),
                ("the flag is off because the deploy failed",
                 "the flag because the deploy failed")):
            assert v2.fold_allowed(a, b), (a, b)

    def test_an_attributive_state_member_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5134).

        `_state_predicate` reads a copula's predicate complement, so a state
        member in ATTRIBUTIVE (pre-nominal) position is never visited and the
        pair folds:

            "the invalid token was rejected" vs "the token was rejected"
              -> fold_allowed True, and rephrase_hit ('c1', 0.8) is above
                 NOOP_MIN_OVERLAP, so the in-capture seam would delete the
                 rival.

        Telling an attributive adjective ("the invalid token") from a
        preposition with a nominal object ("the focus is on quality") is a
        part-of-speech decision: a rule that fired on "member followed by a
        content token" would refuse the pinned prepositional `on` fold.  It is
        the same POS/syntax root as #5139 and is recorded there, not closed
        here.  The refused copula form is asserted beside it, so the pin
        cannot go vacuous.
        """
        assert not v2.fold_allowed("the token is invalid", "the token")
        for a, b in (
                ("the invalid token was rejected", "the token was rejected"),
                ("the off switch is broken", "the switch is broken"),
                ("the offline node was drained", "the node was drained")):
            assert v2.fold_allowed(a, b), (a, b)

    def test_a_contracted_copula_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5134).

        `_apostrophe_free` turns "flag's" into "flags", so a contracted
        be-copula is no `_COPULAS` member and `_state_predicate` sees no
        copula:

            "the flag's off" vs "the flag's"
              -> fold_allowed True, and rephrase_hit ('c1', 0.667) is above
                 NOOP_MIN_OVERLAP, so the seam would delete the rival.

        Unlike `am` — a plain missing set member, closed here — the clitic is
        ambiguous with the possessive ("bob's colour"), so telling the two
        apart is a part-of-speech decision: the same POS root as #5139, where
        it is recorded.  The uncontracted form is asserted beside it, so the
        pin cannot go vacuous.
        """
        assert not v2.fold_allowed("the flag is off", "the flag")
        for a, b in (("the flag's off", "the flag's"),
                     ("it's offline", "it's")):
            assert v2.fold_allowed(a, b), (a, b)

    def test_a_multi_member_polarity_permutation_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5134/#5139).

        The skeleton is a SET, so a pair differing only by the ATTACHMENT of
        two polarity members compares equal and no dimension sees a
        difference:

            "the flag is on and the gate is off"
              vs "the flag is off and the gate is on"      -> folds

        On main this folds too — it is the set-level / attachment blind spot
        of the whole boundary, recorded on #5139 with the clause and
        attributive shapes, and closing it needs the same syntax the fold
        predicate lacks.  The one-sided form beside a SHARED member IS refused
        (the `gate` row of `test_a_one_sided_state_word_is_a_rival_claim`), so
        this pin records a boundary and not a predicate that never fires.
        """
        assert v2.fold_allowed("the flag is on and the gate is off",
                               "the flag is off and the gate is on")
        assert not v2.fold_allowed("the flag is off and the gate is on",
                                   "the flag is off and the gate is off")

    def test_a_role_inversion_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent.

        A marker-free pair that inverts a role carries one multiset and two
        meanings, and a token-level predicate cannot see the inversion.  The
        obvious fix — refuse every pair whose content order differs — is
        WRONG: a legitimate paraphrase reorders freely and #4652 pins that
        "backpressure control is missing from the ingest queue" folds into
        "the ingest queue is missing backpressure control", which is the same
        multiset in a different order.  Separating a reordering from a role
        inversion needs syntax, so it is left to a model (filed as #5131).
        """
        assert v2.fold_allowed("the cat chased the dog",
                               "the dog chased the cat")
        # ... and the reason the shape above cannot be tightened: this pair is
        # the same multiset in a different order and MUST fold (#4652).
        assert v2.fold_allowed(
            "backpressure control is missing from the ingest queue",
            "the ingest queue is missing backpressure control")

    def test_a_month_used_as_a_name_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent.

        A month name is dropped from the content skeleton so that a real date
        change stays a supersedable value change; the cost is that a month
        used as a proper name reaches no dimension that could veto the
        update.  Separating the two needs a model, so it is left to one.
        """
        assert v2.distinguishing_difference(
            "june is our contact", "april is our contact") == "date"
        assert not v2.fold_allowed("june is our contact",
                                   "april is our contact")
        assert v2.supersede_allowed("june is our contact",
                                    "april is our contact")

    def test_a_value_difference_does_not_mask_an_identity_one(self):
        """A pair differing in a number AND in its predicate is a rival claim.

        The number must not license superseding the predicate.  Both dimensions
        are present; the label reports the identity one, because that is the
        one that constrains the decision, and the decisions refuse on the set.
        """
        for prior, candidate in MASKED_IDENTITY:
            label = v2.distinguishing_difference(prior, candidate)
            # `unreadable` is the fail-closed sentinel, so admitting it would
            # let a comparison that CRASHED end-to-end pass as "the value
            # difference did not mask an identity one".
            assert label in v2._IDENTITY_DIMENSIONS and label != "unreadable" \
                , (prior, candidate, label)
            # the value difference is genuinely there too
            assert (v2._value_signature(prior) != v2._value_signature(candidate)
                    or v2._value_bindings(prior)
                    != v2._value_bindings(candidate))
            assert not v2.fold_allowed(prior, candidate)
            assert not v2.supersede_allowed(prior, candidate)


class TestClassifierRefusesTheFold:
    """The against-store site: a rival claim is ADD (or a value UPDATE),
    never a NOOP fold."""

    @pytest.mark.parametrize(("prior", "candidate", "dimension"), NEVER_ACROSS)
    def test_rival_claim_is_never_folded(self, prior, candidate, dimension):
        # The entity mention is drawn from the PRIOR, so the entity gate is
        # satisfied and the boundary — not the gate — is what refuses the fold.
        mention = prior.split()[0]
        d = v2.classify_consolidation(
            {"content": candidate, "about_entities": [mention]},
            [{"id": "pt1", "content": prior}],
            entity_mentions=[mention], current_date="2026-06-16")
        assert d.decision != "NOOP"
        assert v2._token_overlap(prior, candidate) >= v2.NOOP_MIN_OVERLAP

    @pytest.mark.parametrize(("prior", "candidate", "dimension"),
                             [c for c in NEVER_ACROSS
                              if c[2] in IDENTITY_DIMENSIONS])
    def test_an_identity_difference_also_refuses_the_supersede(
            self, prior, candidate, dimension):
        """A rival claim must not be terminalized either: ADD keeps both."""
        mention = prior.split()[0]
        d = v2.classify_consolidation(
            {"content": candidate, "about_entities": [mention]},
            [{"id": "pt1", "content": prior}],
            entity_mentions=[mention], current_date="2026-06-16")
        assert d.decision == "ADD"

    @pytest.mark.parametrize(("prior", "candidate"), MASKED_IDENTITY)
    def test_a_masked_identity_difference_is_still_an_add(
            self, prior, candidate):
        """The classifier must not UPDATE a rival just because a number
        differs too."""
        mention = prior.split()[0]
        d = v2.classify_consolidation(
            {"content": candidate, "about_entities": [mention]},
            [{"id": "pt1", "content": prior}],
            entity_mentions=[mention], current_date="2026-06-16")
        assert d.decision == "ADD"

    def test_a_value_change_still_updates(self):
        d = v2.classify_consolidation(
            {"content": "gym at 5pm", "about_entities": ["gym"]},
            [{"id": "pt1", "content": "gym at 6pm"}],
            entity_mentions=["gym"], current_date="2026-06-16")
        assert d.decision == "UPDATE"

    @pytest.mark.parametrize(("prior", "candidate"), LEGITIMATE_FOLDS)
    def test_the_legitimate_folds_still_fold(self, prior, candidate):
        d = v2.classify_consolidation(
            {"content": candidate, "about_entities": ["gym"]},
            [{"id": "pt1", "content": prior}],
            entity_mentions=["gym"], current_date="2026-06-16")
        assert d.decision == "NOOP"
        assert d.reason == "paraphrase"


class TestInCaptureBandRefusesTheFold:
    """The in-capture site: a refused fold is no hit, so the seam keeps its
    own point instead of deleting it."""

    @pytest.mark.parametrize(("prior", "candidate", "dimension"), NEVER_ACROSS)
    def test_rival_claim_is_never_a_rephrase_hit(
            self, prior, candidate, dimension):
        assert dedup_classify.rephrase_hit([("pt_a", prior)], candidate) is None
        # The band would have taken it — only the boundary refuses it.
        assert v2._token_overlap(prior, candidate) >= v2.NOOP_MIN_OVERLAP

    @pytest.mark.parametrize(("prior", "candidate"), LEGITIMATE_BAND_FOLDS)
    def test_a_restatement_still_folds(self, prior, candidate):
        hit = dedup_classify.rephrase_hit([("pt_a", prior)], candidate)
        assert hit is not None and hit[0] == "pt_a"

    def test_a_capture_restatement_is_still_linked(self):
        """The Phase-D seam's own restatement, with no distinguishing
        difference (a differing connective and a trailing adverb)."""
        canon = [("pt_a",
                  "We should ship the web server first to unblock the mobile "
                  "team.")]
        hit = dedup_classify.rephrase_hit(
            canon, "We should ship the web server first and unblock the "
                   "mobile team now.")
        assert hit is not None and hit[0] == "pt_a"


class TestOneSharedBoundary:
    """One implementation, no drift: both sites resolve the boundary from the
    same function object, so a change to one cannot miss the other."""

    def test_both_sites_use_the_canonical_predicate(self):
        assert dedup_classify.fold_allowed is v2.fold_allowed

    def test_the_identity_dimension_set_is_the_modules(self):
        """The table above is the human-readable list; the module's set is the
        one the classifier consults.  A dimension added to one and not the
        other would silently stop guarding."""
        assert set(v2._IDENTITY_DIMENSIONS) == IDENTITY_DIMENSIONS

    @pytest.mark.parametrize(
        ("prior", "candidate"),
        # A STATIC filter on the dimension, never a call to the predicate under
        # test: filtering with `if not v2.supersede_allowed(a, b)` drops a row
        # silently when the predicate flips, so the parametrization could not
        # fail for the rows it exists to check.  Identity dimensions are the
        # rows `supersede_allowed` must refuse.
        [(a, b) for a, b, d in NEVER_ACROSS if d in IDENTITY_DIMENSIONS]
        + MASKED_IDENTITY)
    def test_the_classifier_cannot_supersede_what_supersede_allowed_refuses(
            self, prior, candidate):
        """The predicate and the classifier's UPDATE gate are one decision.

        ``supersede_allowed`` is the published form of it; the classifier
        reads the identity set from the same ``_boundary`` call, so a change
        to either reaches both.
        """
        assert not v2.supersede_allowed(prior, candidate)
        mention = prior.split()[0]
        d = v2.classify_consolidation(
            {"content": candidate, "about_entities": [mention]},
            [{"id": "pt1", "content": prior}],
            entity_mentions=[mention], current_date="2026-06-16")
        assert d.decision != "UPDATE"

    @pytest.mark.parametrize(("prior", "candidate", "dimension"), NEVER_ACROSS)
    def test_both_sites_agree_on_every_dimension(
            self, prior, candidate, dimension):
        assert dedup_classify.fold_allowed(prior, candidate) \
            == v2.fold_allowed(prior, candidate) is False


class TestFailClosed:
    """Unreadable input preserves both claims — a wrong keep is noise, a
    wrong drop is memory loss."""

    @pytest.mark.parametrize("a,b", [
        ("", "gym at 6pm"),
        ("gym at 6pm", ""),
        ("   ", "gym at 6pm"),
        (None, "gym at 6pm"),
        ("gym at 6pm", None),
    ])
    def test_unreadable_input_refuses_both_destroying_decisions(self, a, b):
        assert v2.distinguishing_difference(a, b) == "unreadable"
        assert v2.fold_allowed(a, b) is False
        assert v2.supersede_allowed(a, b) is False

    @pytest.mark.parametrize("a,b", [
        (None, {}),
        ({}, None),
        (0, []),
    ])
    def test_malformed_shapes_never_raise(self, a, b):
        # Non-str content must not raise: the classifier coerces with str(),
        # so the guard must not be the one place that blows up on LLM-shaped
        # input.  Both sides empty ⇒ unreadable ⇒ nothing may be destroyed.
        assert v2.fold_allowed(a, b) is False
        assert v2.supersede_allowed(a, b) is False

    @pytest.mark.parametrize("a,b", [
        (1, "1"),
        (1, "gym at 6pm"),
        ([1], "x"),
        (None, None),
        ({"a": 1}, 2.5),
    ])
    def test_the_predicate_itself_is_total(self, a, b):
        # The docstring's totality claim, exercised directly: a pair that
        # normalises to equal tokens reaches every sub-predicate, including
        # the one that walks characters.
        assert isinstance(v2.distinguishing_difference(a, b), (str, type(None)))
