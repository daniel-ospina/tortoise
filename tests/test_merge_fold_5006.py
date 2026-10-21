"""#5006 — a near-duplicate fold must not drop the candidate's evidence.

THE DEFECT THIS PINS, IN ONE SENTENCE
-------------------------------------
The paraphrase-NOOP band absorbs one side of a near-duplicate pair **by
threshold** rather than by a decision, and the absorb is lossless only for the
CLAIM: `_boundary` (#5080) refuses a fold across a distinguishing difference,
but nothing looked at the **attachments**. A fold emits no payload point, so it
stamps no link from the surviving Point to the entities the candidate named —
so an entity the candidate names and the prior does not is dropped by the fold
itself. That is a de facto merge that discards one side's evidence, performed
by `NOOP_MIN_OVERLAP`.

WHAT THE RULING SAYS (D12/O4 — `EXTRACTOR-V4-ARCHITECTURE.md` §16.4)
--------------------------------------------------------------------
*"The admissible outcomes are KEEP / NOOP / DISCARD / MERGE — where MERGE
preserves BOTH sides' evidence and attachments."* And §S2.2: **"UNION THE
ATTACHMENTS, NEVER FUSE THE CLAIM."** The asymmetry is the ruling's own: *"a
false merge costs more than a kept near-duplicate"*, and the boundary's
unreadable direction is already **both claims survive**.

⇒ The fix is the same direction one layer out: when the fold would drop an
attachment, the fold is refused and the claim falls through to `ADD`.

⚠️ SCOPE — this is the EVIDENCE-PRESERVATION half of #5006, not the union-merge
itself. The Jev-arbitrated fusion (which keeps ONE point and writes the union of
both sides' attachments onto it) remains unimplemented: `config/v4-mechanisms.yml`
keeps `near_dup_merge` at `state: not-implemented, tracking_issue: 5006`, and
this change deliberately does not flip it. What changes here is only which
folds are admissible: a fold that would lose evidence is no longer taken.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402, RUF100

#: The same pair the existing band test uses, so the ONLY thing that varies
#: across the cases below is the attachment set — the content overlap is held
#: fixed inside the paraphrase band, above NOOP_MIN_OVERLAP and below
#: REVISES_MIN_OVERLAP, with no value token on either side.
PRIOR_CONTENT = "the team meets weekly in main office"
CANDIDATE_CONTENT = "the team meets weekly"


def _prior(**kw) -> dict:
    row = {"id": "pt_prior", "content": PRIOR_CONTENT}
    row.update(kw)
    return row


def _classify(prior: dict, candidate: dict):
    return v2.classify_consolidation(
        candidate, [prior], entity_mentions=["team"])


class TestTheBandStillFoldsBaseline:
    """The precondition: this pair IS a paraphrase fold in the band.

    Without this the cases below would prove nothing — a refusal on a pair that
    was never foldable would be vacuous.
    """

    def test_the_pair_is_in_the_paraphrase_band(self):
        d = _classify(_prior(), {"content": CANDIDATE_CONTENT,
                                 "about_entities": ["team"]})
        assert d.decision == "NOOP", d.evidence
        assert d.reason == "paraphrase"
        assert v2.NOOP_MIN_OVERLAP <= d.overlap < v2.REVISES_MIN_OVERLAP


class TestFoldRefusedWhenItWouldDropEvidence:
    """The guard: an attachment the prior lacks makes the fold inadmissible."""

    def test_new_entity_refuses_the_fold(self):
        """A candidate naming an entity the prior does not is NOT absorbed.

        The prior names only "team"; the candidate also names "office". The
        fold emits no payload point, so folding would drop the candidate's
        link to "office" — the evidence the ruling says merge must preserve.
        """
        d = _classify(_prior(about_entities=["team"]),
                      {"content": CANDIDATE_CONTENT,
                       "about_entities": ["team", "office"]})
        assert d.decision == "ADD", d.evidence
        # Both claims survive — the same direction `_boundary` refuses in.
        assert d.prior_id == ""

    def test_the_refusal_is_per_prior_not_per_candidate(self):
        """One refusing prior must not veto a fold onto a different prior.

        The guard is evaluated inside the per-prior loop (like `_boundary`), so
        a candidate that would lose evidence against prior A can still fold onto
        prior B whose attachments already cover it. Evaluated once against the
        candidate's whole prior set, this case would wrongly come back ADD.
        """
        d = v2.classify_consolidation(
            {"content": CANDIDATE_CONTENT,
             "about_entities": ["team", "office"]},
            [_prior(id="pt_narrow", about_entities=["team"]),
             _prior(id="pt_wide", about_entities=["team", "office"])],
            entity_mentions=["team"])
        assert d.decision == "NOOP", d.evidence
        assert d.prior_id == "pt_wide"


class TestFoldKeptWhenNothingWouldBeLost:
    """The guard must not become "never fold"."""

    def test_candidate_entities_are_a_subset(self):
        d = _classify(_prior(about_entities=["team", "office"]),
                      {"content": CANDIDATE_CONTENT,
                       "about_entities": ["team"]})
        assert d.decision == "NOOP", d.evidence
        assert d.prior_id == "pt_prior"

    def test_identical_attachment_sets(self):
        d = _classify(_prior(about_entities=["team"]),
                      {"content": CANDIDATE_CONTENT,
                       "about_entities": ["team"]})
        assert d.decision == "NOOP", d.evidence

    def test_case_and_whitespace_are_normalised(self):
        d = _classify(_prior(about_entities=["Team", " Office "]),
                      {"content": CANDIDATE_CONTENT,
                       "about_entities": ["office", "TEAM"]})
        assert d.decision == "NOOP", d.evidence


class TestFailOpenOnAnUnknownAttachmentSet:
    """An unreadable/absent set must not refuse a fold.

    "This row names no entities" and "this row's entities could not be read"
    are different facts. Refusing on the second would silently become "never
    fold" wherever the Task-4 enrichment is off, and every S3 prior row without
    `about_entities` (the eval proxy, the minted-operator path) would stop
    folding — a volume regression, not a safety gain.
    """

    def test_prior_without_attachment_set(self):
        d = _classify(_prior(), {"content": CANDIDATE_CONTENT,
                                 "about_entities": ["team", "office"]})
        assert d.decision == "NOOP", d.evidence

    def test_candidate_without_attachment_set(self):
        d = _classify(_prior(about_entities=["team"]),
                      {"content": CANDIDATE_CONTENT})
        assert d.decision == "NOOP", d.evidence

    def test_prior_attachment_set_is_not_a_sequence_of_strings(self):
        """A malformed row is UNREADABLE, not empty — fail open."""
        d = _classify(_prior(about_entities={"team": 1}),
                      {"content": CANDIDATE_CONTENT,
                       "about_entities": ["team", "office"]})
        assert d.decision == "NOOP", d.evidence


class TestTheBoundaryStillWins:
    """#5080 is unchanged: a distinguishing difference refuses the fold first."""

    def test_number_difference_still_refuses(self):
        """The pinned #5080 case (`test_search_keys_attribute_gate`).

        Attachments identical on both sides, so the NEW guard cannot be what
        refuses this fold — the boundary is. That is the point: adding the
        evidence guard must not move which differences the boundary already
        catches, and an attachment-silent pair must still be refused.
        """
        d = v2.classify_consolidation(
            {"content": "gym at 5pm", "about_entities": ["gym"],
             "search_keys": ["gym time", "workout"]},
            [_prior(content="gym at 6pm", about_entities=["gym"],
                    search_keys=["membership cost"])],
            entity_mentions=["gym"], current_date="2026-06-16")
        assert d.decision == "ADD", d.evidence
        assert v2.distinguishing_difference("gym at 6pm", "gym at 5pm") \
            == "number"


class TestTheHelperIsTotal:
    """`_attachment_ids` is the guard's whole input — it must never raise."""

    def test_unusable_values_read_as_unknown(self):
        for bad in (None, "team", 7, {"a": 1}, ["team", 7]):
            assert v2._attachment_ids(bad) is None

    def test_empty_and_blank_sequences_read_as_empty(self):
        assert v2._attachment_ids([]) == frozenset()
        assert v2._attachment_ids(["  ", ""]) == frozenset()
        assert v2._attachment_ids(("Team", "office")) == frozenset(
            {"team", "office"})
