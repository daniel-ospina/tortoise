"""Hermetic regression tests for the embedded-lane evidence producer (#3827).

No graph, no Redis, no subprocess: every test drives the tool's pure classifier
and derivation surfaces. The regression class these exist for is
DUPLICATED VOCABULARY — a second hand-maintained list that silently drifts from
the canonical one:

* the `expected_causes` literal that omitted a cause class added to
  `CAUSE_CLASSES`, making that cause unable to produce a valid RED;
* the bucket vocabularies that kept a `slow-run` no producer could ever emit;
* the mandatory reproducer re-typed instead of derived from
  `FAMILY_REPRODUCERS[0]`;
* a save/AOF cause attributed to a log that contains no fork refusal at all.

Each parity test FAILS if that duplication is reintroduced, rather than merely
exercising the happy path.
"""
from __future__ import annotations

from tools import embedded_evidence as ee

# ── cause vocabulary is DERIVED from CAUSE_CLASSES ──────────────────────────

class TestCauseVocabulary:
    def test_expected_causes_equals_cause_classes_minus_unattributed(self):
        # Bites the `expected_causes` literal regression: a hand-written list
        # that omits a class (e.g. `module-fork-eexist`) fails here the moment
        # that class exists, instead of silently making it unclosable.
        assert set(ee.EXPECTED_CAUSES) == set(ee.CAUSE_CLASSES) - {"unattributed"}

    def test_unattributed_is_never_an_expected_cause(self):
        assert "unattributed" not in ee.EXPECTED_CAUSES
        assert "unattributed" in ee.CAUSE_CLASSES

    def test_expected_causes_preserves_cause_classes_order(self):
        assert list(ee.EXPECTED_CAUSES) == [
            c for c in ee.CAUSE_CLASSES if c != "unattributed"
        ]

    def test_cause_precedence_declares_every_class(self):
        assert set(ee.CAUSE_PRECEDENCE) | {"unattributed"} == set(ee.CAUSE_CLASSES)


# ── bucket vocabulary is canonical, and `slow-run` is gone ─────────────────

class TestBucketVocabulary:
    def test_passing_and_red_partition_the_declared_buckets(self):
        assert set(ee.BUCKET_NAMES) == ee.BUCKETS_PASSING | ee.BUCKETS_RED
        assert not (ee.BUCKETS_PASSING & ee.BUCKETS_RED)

    def test_slow_run_is_not_a_declared_bucket(self):
        # `slow-run` had four consumer sites and zero producers; it was removed
        # rather than wired to a producer the RED half cannot actually run.
        assert "slow-run" not in ee.BUCKET_NAMES
        assert "slow-run" not in ee.BUCKETS_PASSING
        assert "slow-run" not in ee.BUCKETS_RED

    def test_every_consumer_classifies_from_the_canonical_sets(self):
        # A green run is passing; each red bucket is red. Guards a consumer that
        # re-spells the tuple and drifts from the canonical definition.
        assert "green" in ee.BUCKETS_PASSING
        for red in ("unexpected-divergence", "timeout-red", "selection-red"):
            assert red in ee.BUCKETS_RED


# ── mandatory reproducer is DERIVED from FAMILY_REPRODUCERS ────────────────

def test_mandatory_reproducer_is_the_first_family_reproducer():
    assert ee.FAMILY_REPRODUCERS[0] == ee.MANDATORY_REPRODUCER
    assert ee.MANDATORY_REPRODUCER in ee.FAMILY_REPRODUCERS


# ── a fork cause requires the fork refusal (P2-F) ──────────────────────────

class TestForkRefusalIsRequired:
    """A save/AOF line alone is not evidence that a module fork was refused."""

    def test_save_lines_without_the_refusal_are_unattributed(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Background saving started by pid 1",
            "1:M 01 Jan 2026 00:00:00.100 * Background saving terminated with success",
        ]
        cause, evidence = ee.label_cause(lines)
        assert cause == "unattributed"
        assert evidence["fork_refusal"] is False

    def test_aof_lines_without_the_refusal_are_unattributed(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Starting BGREWRITEAOF",
            "1:M 01 Jan 2026 00:00:00.100 * Background AOF rewrite finished successfully",
        ]
        cause, _ = ee.label_cause(lines)
        assert cause == "unattributed"

    def test_save_child_slot_still_labels_when_the_refusal_is_present(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Background saving started by pid 1",
            "1:M 01 Jan 2026 00:00:00.200 # Can't fork for module: File exists",
        ]
        cause, evidence = ee.label_cause(lines)
        assert cause == "save-child-slot"
        assert evidence["fork_refusal"] is True

    def test_aof_rewrite_still_labels_when_the_refusal_is_present(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Starting BGREWRITEAOF",
            "1:M 01 Jan 2026 00:00:00.200 # Can't fork for module: File exists",
        ]
        cause, _ = ee.label_cause(lines)
        assert cause == "aof-rewrite-fork"

    def test_every_real_cause_class_requires_the_refusal(self):
        # Guards a class added to CAUSE_PRECEDENCE from omitting the flag and
        # re-opening the false-attribution hole.
        for name in ee.CAUSE_PRECEDENCE:
            assert ee.CAUSE_CLASSES[name]["requires_fork_refusal"] is True


# ── same_file_list is a DERIVED comparison, never a literal ────────────────

class TestSameFileListIsDerived:
    def test_matching_lists_are_certified(self):
        runs = [{"files": ["tests/a.py", "tests/b.py"]}]
        assert ee._red_file_list_matches(runs, ["tests/a.py", "tests/b.py"]) is True

    def test_differing_lists_are_not_certified(self):
        runs = [{"files": ["tests/a.py"]}]
        assert ee._red_file_list_matches(runs, ["tests/a.py", "tests/b.py"]) is False

    def test_no_red_run_is_not_certified(self):
        assert ee._red_file_list_matches([], ["tests/a.py"]) is False

    def test_a_run_without_a_recorded_list_is_not_certified(self):
        assert ee._red_file_list_matches([{}], ["tests/a.py"]) is False
