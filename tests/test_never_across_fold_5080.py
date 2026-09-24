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

    def test_a_notation_change_is_not_a_value_difference(self):
        """'six' and '6' are one value in two spellings."""
        assert v2.distinguishing_difference("gym at six", "gym at 6") is None

    def test_trailing_punctuation_is_not_a_substitution(self):
        """`_norm` keeps punctuation; the guard must not read 'team.' and
        'team' as two different content tokens."""
        assert v2.distinguishing_difference(
            "we ship the web server first.", "we ship the web server first"
        ) is None

    def test_a_value_difference_does_not_mask_an_identity_one(self):
        """A pair differing in a number AND in its predicate is a rival claim.

        The audit label is the value dimension because it is the first match,
        so a decision made from that label alone would let the number
        difference license superseding the predicate — the prior claim would
        be quietly terminalized by a rival.
        """
        for prior, candidate in MASKED_IDENTITY:
            assert v2.distinguishing_difference(prior, candidate) == "number"
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
