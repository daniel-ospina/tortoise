"""D12/O4 (#5080) — a near-duplicate fold may never cross a distinguishing
difference.

The owner ruling of 2026-09-24 authorises merging near-duplicate claims and
forbids a merge across a difference in a number/quantity, a named entity, a
language, a negation, a condition, or a date/scope.  Both write-path fold sites
enforce that boundary from ONE implementation
(``extractor_v2.fold_allowed`` / ``supersede_allowed``):

* ``classify_consolidation`` — the against-store classifier, and
* ``dedup_classify.rephrase_hit`` — the in-capture band, whose seam deletes a
  folded node outright.

A refused fold produces ADD / no hit, so both claims survive.  A differing
NUMBER or DATE is a new value for one attribute and keeps superseding (UPDATE);
a differing NEGATION, CONDITION, marker SCOPE, LANGUAGE or substituted content
means the two are rival claims, which may be neither folded nor superseded.

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
# value dimension is reported as the audit label (it is the first match), so a
# caller that asked only for that label would let the value difference license
# destroying the identity one.  The decision consults the full identity set.
MASKED_IDENTITY = [
    ("the deploy succeeded at 3pm", "the deploy failed at 5pm"),
    ("the build succeeded at 3pm", "the build did not succeed at 5pm"),
    ("we retried two times and it passed",
     "we retried 3 times and it did not pass"),
    ("he won the 5k in 27:12", "she won the 5k in 25:03"),
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
        """Documented residual, pinned so it cannot go silent (#5139).

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
        """Documented residual, pinned so it cannot go silent (#5139).

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

    def test_a_load_bearing_frame_connective_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5139).

        `and` and `or` are conjunctions — the role that makes them frame words
        and keeps a coordinating paraphrase foldable — and they are also
        operators.  Swapping them changes what the claim asserts, and a set of
        content tokens cannot tell the two uses apart.
        """
        assert v2.fold_allowed("we ship and test", "we ship or test")

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

    def test_a_one_sided_antonym_is_a_known_limit(self):
        """Documented residual, pinned so it cannot go silent (#5134).

        Antonymy is not one of the declared dimensions, and the general rule
        allows a one-sided token because that is the broadening case.  A pair
        where BOTH sides carry a differing state word IS caught (the
        `the flag is on` / `the flag is off` rows above); a state word on one
        side only is not, and telling an antonym from a detail needs a lexicon
        or a model — it belongs to the contradiction classifier, not here.
        """
        assert v2.fold_allowed("the flag is off", "the flag")

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
            assert v2.distinguishing_difference(prior, candidate) \
                in v2._IDENTITY_DIMENSIONS
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
        [(a, b) for a, b, _ in NEVER_ACROSS if not v2.supersede_allowed(a, b)]
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
