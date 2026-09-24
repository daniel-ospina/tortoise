"""Hermetic regression tests for the embedded-lane evidence producer (#3827).

No graph, no Redis. The classifier/derivation tests drive the tool's pure
surfaces; the `_porcelain_digest` tests (#4540) additionally drive REAL git
against a throwaway repo — stubbing the porcelain there is how two defects
shipped green. The regression class these exist for is
DUPLICATED VOCABULARY — a second hand-maintained list that silently drifts from
the canonical one:

* the `expected_causes` literal that omitted a cause class added to
  `CAUSE_CLASSES`, making that cause unable to produce a valid RED;
* the bucket vocabularies that kept a `slow-run` no producer could ever emit;
* the mandatory reproducer re-typed instead of derived from
  `FAMILY_REPRODUCERS[0]`;
* a save/AOF cause attributed to a log that contains no fork refusal at all.

The sibling class is a check that LOOKS like protection but cannot fail, or
cannot bite where the defect lives:

* `same_file_list` derived from the same `files` variable it was meant to check —
  a value compared with itself, `True` in every reachable state;
* a literal written at the CONSUMER (record construction) that a test of the
  module constant alone stays green on, so the consumer-site tests drive
  `_build_record` itself;
* a refusal that states EAGAIN labelled with the EEXIST class name;
* a pid reused across a hang, hidden by a text-wide set difference.

Each parity test FAILS if that duplication is reintroduced, rather than merely
exercising the happy path.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import ClassVar

import pytest

from tools import embedded_evidence as ee


def _nonclosing_record(bucket: str = "unexpected-divergence") -> dict:
    """A record that fails every `closes_issue` conjunct, so a test can inspect the
    REASON LIST for the one conjunct under test.

    It exists to drive the CONSUMER of a constant rather than assert the constant's
    own value: a test of a constant is guaranteed by the module import; a test that
    the consumer reads it can fail.
    """
    return {
        "runs": [{"bucket": bucket, "executed": 1, "tree_moved": False}],
        "selection": {
            "name": "carve-out",
            "files": ["tests/other.py"],
            "expected_causes": [],
        },
        "pin": {
            "worktree_clean": True,
            "head_sha": "0" * 40,
            "commit": "0" * 40,
            "post_review_dirty": False,
        },
        "red": {
            "cause": None,
            "same_file_list": False,
            "at_fixed_commit": {
                "attempted": False,
                "appeared": None,
                "rate_change": False,
                "mutation": None,
                "mutation_operator": None,
                "mutation_target_is_fix_branch": False,
                "mutation_red_returned": False,
                "surface": None,
                "surface_assertion": None,
            },
        },
        "load": {"overlap": False},
        "record_role": "historical-attestation",
        "verdict": {"environment_error": False},
    }


# ── cause vocabulary is DERIVED from CAUSE_CLASSES ──────────────────────────

class TestCauseVocabulary:
    def test_unattributed_is_never_an_expected_cause(self):
        assert "unattributed" not in ee.EXPECTED_CAUSES
        assert "unattributed" in ee.CAUSE_CLASSES

    def test_every_declared_cause_class_is_reachable(self):
        # REPLACES two tautologies. `test_expected_causes_equals_cause_classes_minus_
        # unattributed` compared a derived tuple with its own comprehension, and
        # `test_cause_precedence_declares_every_class` restated the import-time assert
        # (which fires before any test can run). This drives the CONSUMER —
        # `label_cause` — for every declared class. It fails if a class is added to
        # CAUSE_CLASSES without a witness (set equality) or becomes UNREACHABLE
        # because it is missing from CAUSE_PRECEDENCE (declared, documented, and never
        # emitted — the sibling of the class that existed only to invalidate the
        # record). The record-level consumer is covered by
        # `test_record_reads_the_derived_cause_vocabulary`.
        EEXIST = "1:M 01 Jan 2026 00:00:00.200 # Can't fork for module: File exists"
        witnesses = {
            "module-fork-hang": [
                "1:M 01 Jan 2026 00:00:00.000 * Module fork started pid: 7",
                EEXIST,
            ],
            "module-fork-eexist": [EEXIST],
            "aof-rewrite-fork": [
                "1:M 01 Jan 2026 00:00:00.000 * Starting BGREWRITEAOF",
                EEXIST,
            ],
            "save-child-slot": [
                "1:M 01 Jan 2026 00:00:00.000 * Background saving started by pid 1",
                EEXIST,
            ],
        }
        assert set(witnesses) | {"unattributed"} == set(ee.CAUSE_CLASSES)
        for name, lines in witnesses.items():
            assert ee.label_cause(lines)[0] == name, name
        assert ee.label_cause(["1:M no declared pattern here"])[0] == "unattributed"


# ── bucket vocabulary is canonical, and `slow-run` is gone ─────────────────

class TestBucketVocabulary:
    def test_the_exit_code_classifies_every_declared_bucket(self):
        # REPLACES `test_passing_and_red_partition_the_declared_buckets`, which was a
        # tautology: BUCKETS_RED is DEFINED as BUCKET_NAMES - BUCKETS_PASSING, so the
        # partition and disjointness held for any input. This drives the CONSUMER
        # (`exit_code`) with the spec's classification hardcoded: a passing bucket
        # leaves a non-closing record at exit 3, each red bucket is a violation at
        # exit 1. It fails if a bucket is misclassified, if the vocabulary grows
        # without a decision, or if the consumer re-spells the sets and drifts.
        expected = {
            "green": 3,
            "unexpected-divergence": 1,
            "timeout-red": 1,
            "selection-red": 1,
        }
        assert set(expected) == set(ee.BUCKET_NAMES)
        for name, code in expected.items():
            assert ee.exit_code(_nonclosing_record(name)) == code, name

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


# ── an unmeasurable load is not a band (F17) ──────────────────────────────

class TestLoadBands:
    def test_the_unmeasurable_sentinel_is_a_distinct_non_band_state(self):
        # The regression: `load_band(-1.0)` fell through to `LOAD_BANDS[-1][0]`,
        # so an unmeasurable load was reported as the HIGHEST band (L-C). The proxy
        # was silent in exactly the failure case it exists for.
        assert ee.load_band(ee.LOAD_UNMEASURED) == ee.LOAD_BAND_UNMEASURED
        assert ee.load_band(ee.LOAD_UNMEASURED) != "L-C"
        assert ee.LOAD_BAND_UNMEASURED not in {n for n, _, _ in ee.LOAD_BANDS}

    def test_measured_boundary_values_band_deterministically(self):
        assert ee.load_band(11.999) == "L-A"
        assert ee.load_band(12.0) == "L-B"
        assert ee.load_band(23.999) == "L-B"
        assert ee.load_band(24.0) == "L-C"


# ── mandatory reproducer is DERIVED from FAMILY_REPRODUCERS ────────────────

def test_family_declares_the_mandatory_reproducer():
    # REPLACES `test_mandatory_reproducer_is_the_first_family_reproducer`, whose
    # `FAMILY_REPRODUCERS[0] == MANDATORY_REPRODUCER` holds by construction (and
    # whose membership restated it). The plan's rule 1 is a NAMED file, so this can
    # actually fail: redefine the constant or rename the path and it bites.
    assert ee.MANDATORY_REPRODUCER == "tests/test_dr_endpoints.py"
    assert "tests/test_dr_endpoints.py" in ee.FAMILY_REPRODUCERS


def test_closing_rule_reads_the_mandatory_reproducer():
    # The CONSUMER: `closes_issue` reports `reproducer-absent` iff the selection
    # lacks MANDATORY_REPRODUCER. Fails if the conjunct is dropped or checks a
    # re-typed literal (the duplicated-vocabulary class this file exists for).
    present = _nonclosing_record()
    present["selection"] = {
        "name": "family", "files": [ee.MANDATORY_REPRODUCER], "expected_causes": [],
    }
    _, reasons = ee.closes_issue(present)
    assert "reproducer-absent" not in reasons

    absent = _nonclosing_record()
    absent["selection"] = {
        "name": "family", "files": ["tests/other.py"], "expected_causes": [],
    }
    _, reasons = ee.closes_issue(absent)
    assert "reproducer-absent" in reasons


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


# ── the classifier does not over-claim (F3), and does not lose a hang (F4) ──

class TestCauseClassifierPrecision:
    def test_eagain_refusal_is_not_labelled_module_fork_eexist(self):
        # `Can't fork for module:` fronts EVERY errno. The class named
        # `module-fork-eexist` asserts `File exists`; an EAGAIN refusal must not be
        # given a label whose name states a cause the log does not.
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 # Can't fork for module: "
            "Resource temporarily unavailable",
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "unattributed"
        assert ev["fork_refusal"] is True
        assert ev["eexist_refusal"] is False

    def test_eexist_refusal_still_labels_module_fork_eexist(self):
        lines = ["1:M 01 Jan 2026 00:00:00.000 # Can't fork for module: File exists"]
        cause, ev = ee.label_cause(lines)
        assert cause == "module-fork-eexist"
        assert ev["eexist_refusal"] is True

    def test_pid_reuse_does_not_hide_an_unexited_module_fork(self):
        # A SET difference over the whole log collapses this: 123 is in `started`
        # AND in `exited`, so `started - exited` is empty and a genuine hang was
        # reported as `module-fork-eexist`. The ordered running count sees it.
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Module fork started pid: 123",
            "1:M 01 Jan 2026 00:00:00.100 * Module fork exited pid: 123",
            "1:M 01 Jan 2026 00:00:00.200 * Module fork started pid: 123",
            "1:M 01 Jan 2026 00:00:00.300 # Can't fork for module: File exists",
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "module-fork-hang"
        assert ev["module_forks_unexited"] == ["123"]

    def test_a_matched_start_exit_pair_is_not_an_unexited_fork(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Module fork started pid: 7",
            "1:M 01 Jan 2026 00:00:00.100 * Module fork exited pid: 7",
            "1:M 01 Jan 2026 00:00:00.200 # Can't fork for module: File exists",
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "module-fork-eexist"
        assert ev["module_forks_unexited"] == []

    def test_no_global_set_difference_evidence_field(self):
        # `module_fork_exited_absent` was `bool(started) and not exited` over the
        # GLOBAL pid sets, so a stale `exited pid: 42` plus a `started pid: 7` that
        # never exited reported False — wrong per-pid, and the record is read by a
        # human. It was consumed by nothing (verified), so it is DELETED.
        # NOTE: `module_forks_unexited` is NOT the hang signal — it samples
        # end-of-log, so a fork started AFTER the refusal appears in it. The
        # witness is `module_forks_outstanding_at_refusal` (the same ordered
        # counter sampled AT each EEXIST refusal line). `unexited` is retained
        # as evidence only. This guards against reintroducing a misleading
        # evidence field.
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Module fork exited pid: 42",
            "1:M 01 Jan 2026 00:00:00.100 * Module fork started pid: 7",
        ]
        _, ev = ee.label_cause(lines)
        assert "module_fork_exited_absent" not in ev
        assert ev["module_forks_unexited"] == ["7"]


class TestForkClassesRequireEexist:
    """Every slot-occupancy cause class needs the EEXIST text, not just
    `module-fork-eexist`.

    The bare `Can't fork for module:` prefix fronts EVERY errno. Each of these
    classes is in `EXPECTED_CAUSES`, so a bare-prefix match lets an EAGAIN refusal
    produce a CLOSING record carrying a cause the log never states. Each class's own
    comment asserts the child "occupies the slot" (the EEXIST `moduleForkChildPid`
    check), and the plan doc's `module-fork-hang` declaration spells `File exists`.
    """

    EAGAIN = (
        "1:M 01 Jan 2026 00:00:00.200 # Can't fork for module: "
        "Resource temporarily unavailable"
    )

    def test_every_cause_class_requires_the_eexist_text(self):
        # A class added to CAUSE_PRECEDENCE without the flag re-opens the
        # false-attribution hole, so this fails the moment one is added.
        for name in ee.CAUSE_PRECEDENCE:
            assert ee.CAUSE_CLASSES[name].get("requires_eexist_refusal") is True, name

    def test_save_child_slot_eagain_falls_to_unattributed(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Background saving started by pid 1",
            self.EAGAIN,
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "unattributed"
        assert ev["fork_refusal"] is True and ev["eexist_refusal"] is False

    def test_aof_rewrite_eagain_falls_to_unattributed(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Starting BGREWRITEAOF",
            self.EAGAIN,
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "unattributed"
        assert ev["fork_refusal"] is True and ev["eexist_refusal"] is False

    def test_module_fork_hang_eagain_falls_to_unattributed(self):
        # A hang is only evidence FOR THE REFUSAL when the refusal is the
        # slot-check EEXIST; an EAGAIN refusal is a fork() resource-limit failure,
        # a different mechanism. The unexited pid stays in evidence; only the
        # unsupported causation claim is withheld.
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Module fork started pid: 7",
            self.EAGAIN,
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "unattributed"
        assert ev["module_forks_unexited"] == ["7"]

    def test_save_child_slot_still_labels_on_an_eexist_refusal(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Background saving started by pid 1",
            "1:M 01 Jan 2026 00:00:00.200 # Can't fork for module: File exists",
        ]
        assert ee.label_cause(lines)[0] == "save-child-slot"

    def test_aof_rewrite_still_labels_on_an_eexist_refusal(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Starting BGREWRITEAOF",
            "1:M 01 Jan 2026 00:00:00.200 # Can't fork for module: File exists",
        ]
        assert ee.label_cause(lines)[0] == "aof-rewrite-fork"

    def test_module_fork_hang_still_labels_on_an_eexist_refusal(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Module fork started pid: 7",
            "1:M 01 Jan 2026 00:00:00.200 # Can't fork for module: File exists",
        ]
        assert ee.label_cause(lines)[0] == "module-fork-hang"


class TestModuleForkHangIsReachableOnRealEvidence:
    """The class must fire on the line the daemon ACTUALLY emits.

    Measured over the 1120 real `redis.log`s collected under `/tmp/pi3827-*`:
    `Module fork started pid:` occurs in **0** of them, so `module-fork-hang` keyed
    only on the started/exited counter was UNREACHABLE on real evidence. The line the
    daemon does emit for this class is `There is a module fork child. Killing it!` —
    the plan doc's declared second `requires_lines` entry — and the corpus log that
    carries it (together with 10 EEXIST refusals) was labelled `module-fork-eexist`,
    the label reserved for a PRIOR instance's child: a proxy silent in exactly the
    case it exists to cover.
    """

    KILLING = "1:M 01 Jan 2026 00:00:01.000 # There is a module fork child. Killing it!"
    EEXIST = "1:M 01 Jan 2026 00:00:00.200 # Can't fork for module: File exists"

    def test_hang_is_reachable_without_any_started_line(self):
        lines = [self.EEXIST, self.KILLING]
        cause, ev = ee.label_cause(lines)
        assert cause == "module-fork-hang"
        assert ev["module_fork_child_killed"] is True
        assert ev["module_forks_started"] == []

    def test_the_killing_line_alone_is_not_an_attributable_cause(self):
        # The witness proves an unexited child existed; it is not itself a fork
        # refusal, so a log that only reports the child states no cause this tool can
        # attribute. This is the other half of the corpus: 2 logs carry the killing
        # line, and only ONE of them also carries a refusal.
        cause, ev = ee.label_cause([self.KILLING])
        assert cause == "unattributed"
        assert ev["module_fork_child_killed"] is True
        assert ev["fork_refusal"] is False

    def test_a_started_line_without_an_exit_still_labels_a_hang(self):
        # The ordered counter witness is NOT dropped: a log that DOES carry the
        # lifecycle lines must still label the hang, or making the class reachable on
        # real evidence would have traded one blind spot for another.
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Module fork started pid: 7",
            self.EEXIST,
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "module-fork-hang"
        assert ev["module_fork_child_killed"] is False
        assert ev["module_forks_unexited"] == ["7"]


class TestKillingLineWitnessRequiresCausation:
    """The shutdown assertion is a WEAK witness: it must not relabel a refusal
    that a save/AOF child better explains.

    `There is a module fork child. Killing it!` proves a child was outstanding at
    SHUTDOWN, not at the refusal. Used alone it was a co-occurrence test, so a log
    whose EEXIST came from a background save was relabelled `module-fork-hang`
    because some unrelated module child lingered. These cases are the reviewer's
    reproduction (`/tmp/revsyn/*`). The real log's shape — EEXIST + the killing line
    and NOTHING else — must still label the hang, so the fix withholds the witness
    only when another explanation is present.
    """

    KILLING = "1:M 01 Jan 2026 00:05:00.000 # There is a module fork child. Killing it!"
    EEXIST = "1:M 01 Jan 2026 00:00:00.200 # Can't fork for module: File exists"

    def test_a_save_child_present_with_a_later_killing_line_is_not_a_hang(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Background saving started by pid 1",
            self.EEXIST,
            "1:M 01 Jan 2026 00:00:01.000 * Background saving terminated with success",
            self.KILLING,
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "save-child-slot"
        assert ev["module_fork_child_killed"] is True

    def test_an_aof_child_present_with_a_later_killing_line_is_not_a_hang(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Starting BGREWRITEAOF",
            self.EEXIST,
            self.KILLING,
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "aof-rewrite-fork"
        assert ev["module_fork_child_killed"] is True

    def test_an_exited_fork_with_a_later_killing_line_is_not_a_hang(self):
        # The killing line is the only unexited-child witness. With a fork exited in
        # between, the shutdown child could be a LATER, unrelated instance, so the
        # witness cannot establish that the refusal child is the one killed.
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Module fork exited pid: 7",
            self.EEXIST,
            self.KILLING,
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "module-fork-eexist"
        assert ev["module_fork_child_killed"] is True

    def test_the_counter_witness_is_unaffected_by_a_co_present_save_child(self):
        # The fix must not over-constrain: an outstanding module fork is DIRECT
        # evidence the RM_Fork slot was held, so it labels the hang even when a save
        # child is also present. Weakening this back toward the save class would
        # re-open the blind spot the class exists to cover.
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Background saving started by pid 1",
            "1:M 01 Jan 2026 00:00:00.100 * Module fork started pid: 7",
            self.EEXIST,
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "module-fork-hang"
        assert ev["module_forks_unexited"] == ["7"]


# ── the counter witness is read AT the refusal, not at end-of-log ──────────

class TestModuleForkHangWitnessIsTemporal:
    """`module-fork-hang` says a fork held the slot when RM_Fork CHECKED it.

    An ordered counter evaluated at END-OF-LOG answers a different question — "was a
    fork outstanding at shutdown" — and both logs below end with an unexited pid. At
    end-of-log they were labelled `module-fork-hang`; at the refusal line no fork was
    outstanding in either, so neither log contains the causation the label asserts.
    """

    EEXIST = "1:M 01 Jan 2026 00:00:00.200 # Can't fork for module: File exists"

    def test_a_fork_starting_after_the_refusal_is_not_a_hang(self):
        # The refusal is the save child's; the module fork begins afterwards, so it
        # cannot be the reason RM_Fork failed.
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Background saving started by pid 1",
            self.EEXIST,
            "1:M 01 Jan 2026 00:00:01.000 * Background saving terminated with success",
            "1:M 01 Jan 2026 00:00:02.000 * Module fork started pid: 7",
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "save-child-slot"
        # The end-of-log leftover is still REPORTED — it is evidence, just not evidence
        # of this refusal — so the assertions cover both the label and the temporal
        # field, and the leftover is what the pre-fix read mistook for a witness.
        assert ev["module_forks_unexited"] == ["7"]
        assert ev["module_forks_outstanding_at_refusal"] == []

    def test_a_fork_exiting_before_the_refusal_and_restarting_after_is_not_a_hang(self):
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Module fork started pid: 7",
            "1:M 01 Jan 2026 00:00:00.100 * Module fork exited pid: 7",
            self.EEXIST,
            "1:M 01 Jan 2026 00:00:01.000 * Module fork started pid: 7",
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "module-fork-eexist"
        assert ev["module_forks_unexited"] == ["7"]
        assert ev["module_forks_outstanding_at_refusal"] == []

    def test_a_fork_outstanding_at_the_refusal_still_labels_the_hang(self):
        # The temporal read must not make the class unreachable: a fork outstanding
        # AT the refusal is direct evidence the slot was held. A second fork started
        # afterwards is a later, unrelated leftover.
        lines = [
            "1:M 01 Jan 2026 00:00:00.000 * Module fork started pid: 7",
            self.EEXIST,
            "1:M 01 Jan 2026 00:00:01.000 * Module fork started pid: 8",
        ]
        cause, ev = ee.label_cause(lines)
        assert cause == "module-fork-hang"
        assert ev["module_forks_outstanding_at_refusal"] == ["7"]
        assert ev["module_forks_unexited"] == ["7", "8"]


# ── same_file_list compares the red run's OWN observed files ──────────────

class TestSameFileListIsDerived:
    """The comparison's two sides must be INDEPENDENT data.

    `_run_once` records `files` as a copy of the selection, so a comparison against
    that field is a value compared with itself — `True` in every reachable state.
    The independent side is each run's junit-observed file set, carried as a
    `_JunitObservation` so the selection cannot be substituted for it.
    """

    @staticmethod
    def _obs(observed, failing, source="junit-1.xml"):
        return ee._JunitObservation(tuple(observed), tuple(failing), source)

    def test_a_red_that_observed_the_selection_is_certified(self):
        runs = [{"observed_files": self._obs(["tests/a.py", "tests/b.py"], ["tests/a.py"])}]
        assert ee._red_file_list_matches(runs, ["tests/a.py", "tests/b.py"]) is True

    def test_a_red_that_observed_a_different_file_list_is_not_certified(self):
        # The mutation that re-derives the comparison from the `files` copy makes
        # this assertion fail: `files` would match the selection while the run's
        # OWN junit shows it only ever ran one of the two files.
        runs = [{
            "files": ["tests/a.py", "tests/b.py"],
            "observed_files": self._obs(["tests/a.py"], ["tests/a.py"]),
        }]
        assert ee._red_file_list_matches(runs, ["tests/a.py", "tests/b.py"]) is False

    def test_no_red_run_is_not_certified(self):
        assert ee._red_file_list_matches([], ["tests/a.py"]) is False

    def test_a_run_without_observed_files_is_not_certified(self):
        assert ee._red_file_list_matches([{}], ["tests/a.py"]) is False

    def test_a_selection_copy_is_not_an_observation(self):
        # The INDEPENDENCE PROPERTY, at the consumer seam. A record whose observed
        # side is the selection — which is exactly what `list(files)` is, a plain
        # `list[str]` — carries no independent evidence, so it cannot certify, even
        # though the value equals the selection. The type is what makes the
        # substitution structurally unreachable rather than merely unobserved.
        #
        # `failing_files` is present so this also bites a fallback that re-wraps the
        # selection in the observation type: without it the forged observation would
        # carry no failing file and the last check would reject it for the wrong
        # reason, leaving the forging mutation free to pass.
        runs = [{
            "files": ["tests/a.py"],
            "failing_files": ["tests/a.py"],
            "observed_files": ["tests/a.py"],
        }]
        assert ee._red_file_list_matches(runs, ["tests/a.py"]) is False

    def test_an_empty_observation_cannot_be_filled_from_the_selection(self):
        # The ONLY record state in which a consumer-side fallback
        # (`if not observed: observed = {... r["files"] ...}`) can change the answer:
        # an observation that observed no file yet recorded a failing one. The junit
        # reader cannot produce it (failing is always a subset of observed), so the
        # state is unreachable from the producer — but the fallback is precisely the
        # re-coupling this function exists to refuse, so the hole is closed here
        # rather than left to the reader's consistency.
        runs = [{
            "files": ["tests/a.py", "tests/b.py"],
            "observed_files": self._obs([], ["tests/a.py"]),
        }]
        assert ee._red_file_list_matches(runs, ["tests/a.py", "tests/b.py"]) is False

    def test_a_red_that_failed_in_no_file_is_not_certified(self):
        runs = [{"observed_files": self._obs(["tests/a.py"], [])}]
        assert ee._red_file_list_matches(runs, ["tests/a.py"]) is False


class TestJunitFileExtraction:
    """`_junit_test_files` is the independent data source the conjunct reads."""

    XUNIT1 = (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        '<testcase classname="tests.test_a" name="test_ok" file="tests/test_a.py" '
        'line="1" time="0.1" />'
        '<testcase classname="tests.test_b" name="test_bad" file="tests/test_b.py" '
        'line="1" time="0.1"><failure message="boom">x</failure></testcase>'
        '</testsuite></testsuites>'
    )

    def test_observed_and_failing_files_come_from_the_run_own_junit(self, tmp_path: Path):
        p = tmp_path / "junit.xml"
        p.write_text(self.XUNIT1)
        obs = ee._junit_test_files(p)
        assert obs.observed == ("tests/test_a.py", "tests/test_b.py")
        assert obs.failing == ("tests/test_b.py",)
        assert obs.source == str(p)

    def test_a_missing_junit_yields_an_empty_observation(self, tmp_path: Path):
        missing = tmp_path / "absent.xml"
        obs = ee._junit_test_files(missing)
        assert obs.observed == () and obs.failing == ()
        assert obs.source == str(missing)

    def test_a_junit_without_file_attributes_yields_an_empty_observation(
        self, tmp_path: Path
    ):
        # This is exactly what pytest's default xunit2 emits — and it is why the
        # run command must pass `-o junit_family=xunit1`.
        p = tmp_path / "xunit2.xml"
        p.write_text(
            '<?xml version="1.0"?><testsuites><testsuite>'
            '<testcase classname="tests.test_a" name="test_ok" time="0.1" />'
            '</testsuite></testsuites>'
        )
        obs = ee._junit_test_files(p)
        assert obs.observed == () and obs.failing == ()

    def test_the_run_command_requests_xunit1(self):
        cmd = ee._pytest_cmd(
            ["tests/a.py"], Path("/tmp/junit.xml"), "not track_b", 60
        )
        assert "junit_family=xunit1" in cmd


class TestPersistedObservationRoundTrips:
    """A record written to disk must reload into something the conjunct can read.

    `_JunitObservation` is a NamedTuple, so `json.dumps` used to write an anonymous
    `[observed, failing, source]` triple. Reloaded, it was a `list`, `isinstance`
    was False, and `_red_file_list_matches` failed closed on EVERY persisted record —
    so a future verifier re-evaluating a record would reject a red that genuinely
    ran the selection. No consumer exists today, which is why this is integrity, not
    a live gate.
    """

    FILES: ClassVar[list[str]] = ["tests/a.py", "tests/b.py"]

    def _record(self):
        return {
            "runs": [{
                "observed_files": ee._JunitObservation(
                    ("tests/a.py", "tests/b.py"), ("tests/a.py",), "junit-1.xml"
                )
            }]
        }

    def test_a_persisted_record_rehydrates_and_still_certifies(self, tmp_path: Path):
        out = tmp_path / "record.json"
        ee._write_record(self._record(), out)
        reloaded = json.loads(out.read_text())
        persisted = reloaded["runs"][0]["observed_files"]
        assert persisted == {
            "observed": ["tests/a.py", "tests/b.py"],
            "failing": ["tests/a.py"],
            "source": "junit-1.xml",
        }
        assert ee._red_file_list_matches(reloaded["runs"], self.FILES) is True

    def test_the_anonymous_triple_is_not_accepted_on_read(self):
        # A pre-fix record's shape. It holds the same values but carries no labels,
        # so it is not an observation and stays rejected rather than being guessed at.
        legacy = [{"observed_files": [
            ["tests/a.py"], ["tests/a.py"], "junit-1.xml",
        ]}]
        assert ee._red_file_list_matches(legacy, ["tests/a.py"]) is False

    def test_a_malformed_labelled_object_is_not_accepted(self):
        assert ee._red_file_list_matches(
            [{"observed_files": {"observed": ["tests/a.py"]}}], ["tests/a.py"]
        ) is False

    # Key NAMES alone were validated, so the value-typed shapes below were accepted
    # (measured on the pre-fix code: `source: null`, `source: 123`, and a `failing`
    # STRING with `source` present each rehydrated and made `_red_file_list_matches`
    # return True). A string is iterable, so `tuple("tests/a.py")` iterated to its
    # CHARACTERS and a one-character value could certify a red.
    HOSTILE_SHAPES = (
        {"observed": ["tests/a.py"], "failing": ["tests/a.py"], "source": None},
        {"observed": ["tests/a.py"], "failing": ["tests/a.py"], "source": 123},
        {"observed": ["tests/a.py"], "failing": "tests/a.py", "source": "junit-1.xml"},
        {"observed": "tests/a.py", "failing": ["tests/a.py"], "source": "junit-1.xml"},
        {"observed": ["tests/a.py"], "failing": [7], "source": "junit-1.xml"},
        {"observed": ["tests/a.py"], "failing": ["tests/a.py"]},
        {  # an extra key is not the persisted shape either
            "observed": ["tests/a.py"], "failing": ["tests/a.py"],
            "source": "junit-1.xml", "extra": 1,
        },
    )

    def test_hostile_observation_shapes_are_rejected_without_raising(self):
        for shape in self.HOSTILE_SHAPES:
            assert ee._observation_from_json(shape) is None, shape
            assert ee._red_file_list_matches(
                [{"observed_files": shape}], ["tests/a.py"]
            ) is False, shape

    def test_a_string_failing_file_is_rejected_not_iterated(self):
        # The headline shape: `source` IS a `str`, so the key-name guard saw the
        # labelled object it accepts, while `tuple("tests/a.py")` silently produced
        # the characters `t`,`e`,`s`,...
        shape = {
            "observed": ["tests/a.py"], "failing": "tests/a.py",
            "source": "junit-1.xml",
        }
        assert ee._observation_from_json(shape) is None
        assert ee._red_file_list_matches(
            [{"observed_files": shape}], ["tests/a.py"]
        ) is False


class TestBothJunitReadersTolerateATruncatedFile:
    """A child killed while pytest flushes its junit leaves TRUNCATED XML.

    `_junit_test_files` already caught `ParseError`; `_read_junit_counts` did not,
    so on the TIMEOUT path — precisely the case that exists when things are going
    wrong — the harness raised a traceback instead of recording an outcome.
    """

    TRUNCATED = (
        '<?xml version="1.0"?><testsuites><testsuite>'
        '<testcase file="tests/a.py" name="test_x"'
    )

    def test_truncated_junit_does_not_raise_and_is_reported(self, tmp_path: Path):
        p = tmp_path / "truncated.xml"
        p.write_text(self.TRUNCATED)
        obs = ee._junit_test_files(p)
        assert obs.observed == () and obs.failing == ()
        counts = ee._read_junit_counts(p)
        assert counts["observed"] == 0
        assert counts["junit_parse_error"], "the failure must be recorded, not swallowed"

    def test_a_missing_junit_reports_no_parse_error(self, tmp_path: Path):
        assert ee._read_junit_counts(tmp_path / "absent.xml")["junit_parse_error"] is None

    def test_a_truncated_junit_records_an_outcome_instead_of_raising(
        self, tmp_path: Path, monkeypatch
    ):
        # The record must say what happened. Drive the timeout path with the
        # truncated file the killed child left behind.
        (tmp_path / "junit-1.xml").write_text(self.TRUNCATED)
        monkeypatch.setattr(ee, "_child_env", lambda rr: {})
        monkeypatch.setattr(ee, "_snapshot_redis_logs", lambda rr: [])
        monkeypatch.setattr(ee, "load1", lambda: 1.0)

        class TimeoutProc:
            returncode = 124

            def communicate(self, timeout=None):
                if timeout == 30:
                    return ("", None)
                raise ee.subprocess.TimeoutExpired(cmd="pytest", timeout=timeout)

            def kill(self):
                pass

        monkeypatch.setattr(ee.subprocess, "Popen", lambda *a, **k: TimeoutProc())
        rec = ee._run_once(["tests/a.py"], tmp_path, tmp_path, 1, "m", 1)
        assert rec["bucket"] == "timeout-red"
        assert rec["junit_parse_error"]


class TestRunOnceWiresTheIndependentFileList:
    """The PRODUCER must read `observed_files` from the run's OWN junit.

    The helper-level tests feed hand-built dicts, so they stay green if
    `_run_once` re-couples `observed_files` to the `files` selection variable — the
    exact vacuity `same_file_list` exists to prevent. These drive `_run_once` itself:
    one with a REAL junit on disk (the guard must be able to PASS — over-tight is a
    defect too), one with none (the production state the guard exists for: a red run
    whose junit was never written), where the field must be an EMPTY observation and
    never the selection.
    """

    @staticmethod
    def _drive(tmp_path: Path, monkeypatch, returncode: int) -> dict:
        monkeypatch.setattr(ee, "_child_env", lambda rr: {})
        monkeypatch.setattr(ee, "_snapshot_redis_logs", lambda rr: [])
        monkeypatch.setattr(ee, "load1", lambda: 1.0)

        class Proc:
            def __init__(self):
                self.returncode = returncode

            def communicate(self, timeout=None):
                return ("", None)

            def kill(self):
                pass

        monkeypatch.setattr(ee.subprocess, "Popen", lambda *a, **k: Proc())
        return ee._run_once(
            ["tests/selection_x.py", "tests/selection_y.py"],
            tmp_path, tmp_path, 1, "m", 60,
        )

    def test_a_red_that_ran_the_selection_is_certified_from_its_own_junit(
        self, tmp_path: Path, monkeypatch
    ):
        (tmp_path / "junit-1.xml").write_text(
            '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
            '<testcase classname="t" name="ok" file="tests/selection_x.py" time="0.1" />'
            '<testcase classname="t" name="bad" file="tests/selection_y.py" time="0.1">'
            '<failure message="boom">x</failure></testcase>'
            '</testsuite></testsuites>'
        )
        rec = self._drive(tmp_path, monkeypatch, returncode=1)
        assert rec["bucket"] == "unexpected-divergence"
        assert rec["observed_files"].observed == ("tests/selection_x.py", "tests/selection_y.py")
        assert rec["observed_files"].failing == ("tests/selection_y.py",)
        assert rec["observed_files"].source.endswith("junit-1.xml")
        assert ee._red_file_list_matches(
            [rec], ["tests/selection_x.py", "tests/selection_y.py"]
        ) is True

    def test_a_red_with_no_junit_observes_nothing_and_cannot_certify(
        self, tmp_path: Path, monkeypatch
    ):
        # NO `junit-1.xml` is written: the real reader runs and returns an EMPTY
        # observation. This is the state a fallback at the producer seam exists to
        # hide — under `if not observed: observed_files = list(files)` the field is the
        # SELECTION, so the isinstance assertion below fails and the vacuous
        # `same_file_list = True` cannot be restored.
        rec = self._drive(tmp_path, monkeypatch, returncode=1)
        assert rec["bucket"] == "selection-red"  # a red whose junit was never written
        assert isinstance(rec["observed_files"], ee._JunitObservation)
        assert rec["observed_files"].observed == ()
        assert rec["observed_files"].failing == ()
        assert rec["observed_files"].source.endswith("junit-1.xml")
        assert rec["files"] == ["tests/selection_x.py", "tests/selection_y.py"]
        assert ee._red_file_list_matches(
            [rec], ["tests/selection_x.py", "tests/selection_y.py"]
        ) is False


# ── the CONSUMER site (record construction) reads the canonical constants ──

class TestRecordConstructionReadsTheCanonicalConstants:
    """The ORIGINAL defect lived at the CONSUMER, not at the module constant.

    `expected_causes` and `same_file_list` were hand-written into the record dict
    inside `_build_record`. A test that only compares `ee.EXPECTED_CAUSES` to
    `ee.CAUSE_CLASSES` stays GREEN when a literal is written back at the consumer,
    so these tests drive `_build_record` itself.
    """

    @staticmethod
    def _args(**over):
        import argparse

        a = argparse.Namespace(
            selection="family",
            n=2,
            ref=None,
            pairing_ref=None,
            marker=ee.DEFAULT_MARKER,
            load_ceiling=1e9,
            run_timeout=1,
            record_role="historical-attestation",
            record_out=None,
            cmd="run",
            environment_error=None,
        )
        for k, v in over.items():
            setattr(a, k, v)
        return a

    @staticmethod
    def _run(files, run_id, bucket, observed=None, failing=None):
        red = bucket != "green"
        obs = list(files) if observed is None else list(observed)
        fail = [] if not red else (
            list(failing) if failing is not None else [files[0]]
        )
        return {
            "run_id": run_id,
            "bucket": bucket,
            "files": list(files),
            "returncode": 1 if red else 0,
            "step_wall_s": 1.0,
            "observed": len(files),
            "executed": len(files),
            "skipped": 0,
            "load": {"before": 1.0, "after": 1.0, "band": "L-A"},
            "tree_moved": False,
            "redis_log_cause": "module-fork-eexist" if red else None,
            "cause_evidence": {},
            "timed_out": bucket == "timeout-red",
            # The observed side the matcher reads is a junit OBSERVATION — the only
            # type `_red_file_list_matches` accepts — never the selection.
            "observed_files": ee._JunitObservation(
                tuple(obs), tuple(fail), f"junit-{run_id}.xml"
            ),
        }

    def _record(self, monkeypatch, runs):
        monkeypatch.setattr(
            ee,
            "_manifest_receipt",
            lambda files, marker, out_dir: {
                "path": "stub",
                "digest": "sha256:0",
                "count": len(files),
                "unique_count": len(files),
                "marker": marker,
            },
        )
        monkeypatch.setattr(
            ee,
            "_run_once",
            lambda files, measured_root, run_root, run_id, marker, timeout: runs[run_id - 1],
        )
        monkeypatch.setattr(ee, "load1", lambda: 1.0)
        monkeypatch.setattr(ee, "_git", lambda *a, cwd=None: "0" * 40)
        monkeypatch.setattr(ee, "_porcelain_digest", lambda *a, **k: ("sha256:0", False))
        monkeypatch.setattr(ee, "_tool_version", lambda: "blob0")
        return ee._build_record(self._args())

    def test_record_reads_the_derived_cause_vocabulary(self, monkeypatch):
        # Re-writing the stale three-cause literal at the record-construction site
        # — the original defect — fails HERE.
        files = list(ee.FAMILY_REPRODUCERS)
        runs = [
            self._run(files, 1, "unexpected-divergence"),
            self._run(files, 2, "unexpected-divergence"),
        ]
        rec = self._record(monkeypatch, runs)
        assert rec["selection"]["expected_causes"] == list(ee.EXPECTED_CAUSES)

    def test_record_reads_the_red_runs_own_file_list(self, monkeypatch):
        files = list(ee.FAMILY_REPRODUCERS)
        runs = [
            self._run(files, 1, "unexpected-divergence"),
            self._run(files, 2, "unexpected-divergence"),
        ]
        rec = self._record(monkeypatch, runs)
        assert rec["red"]["same_file_list"] is True

    def test_an_unmeasured_run_cannot_pass_the_overlap_conjunct(self, monkeypatch):
        # The producer must not present an unmeasurable load as a band. Both runs
        # carry the sentinel's non-band state, so no overlap can be claimed and the
        # closing rule reports it.
        files = list(ee.FAMILY_REPRODUCERS)
        runs = [
            self._run(files, 1, "unexpected-divergence"),
            self._run(files, 2, "unexpected-divergence"),
        ]
        for r in runs:
            r["load"]["band"] = ee.load_band(ee.LOAD_UNMEASURED)
        rec = self._record(monkeypatch, runs)
        assert rec["load"]["overlap"] is False
        _, reasons = ee.closes_issue(rec)
        assert "load-bands-do-not-overlap" in reasons

    def test_a_measured_lc_run_still_passes_the_overlap_conjunct(self, monkeypatch):
        # The guard must not be over-tight: a genuinely measured L-C run (this
        # host's regime) still overlaps and is not rejected for the band reason.
        files = list(ee.FAMILY_REPRODUCERS)
        runs = [
            self._run(files, 1, "unexpected-divergence"),
            self._run(files, 2, "unexpected-divergence"),
        ]
        for r in runs:
            r["load"]["band"] = ee.load_band(30.0)
        assert ee.load_band(30.0) == "L-C"
        rec = self._record(monkeypatch, runs)
        assert rec["load"]["overlap"] is True
        _, reasons = ee.closes_issue(rec)
        assert "load-bands-do-not-overlap" not in reasons

    def test_record_rejects_a_red_that_observed_a_different_file_list(self, monkeypatch):
        files = list(ee.FAMILY_REPRODUCERS)
        runs = [
            self._run(files, 1, "unexpected-divergence", observed=files[:1]),
            self._run(files, 2, "unexpected-divergence"),
        ]
        rec = self._record(monkeypatch, runs)
        assert rec["red"]["same_file_list"] is False
        ok, reasons = ee.closes_issue(rec)
        assert ok is False
        assert "red-file-list-differs" in reasons


def _git_env() -> dict[str, str]:
    """Environment for the FIXTURE's git calls **and** for the MEASURED ones.

    Every `GIT_*` variable is stripped: `GIT_DIR`, `GIT_WORK_TREE` and
    `GIT_INDEX_FILE` would otherwise aim git at a different repository or index
    than `cwd`. The global and system config are redirected to `/dev/null`, so the
    runner's `core.autocrlf` / `core.fsmonitor` / `core.untrackedCache` /
    `core.excludesFile` / `status.*` / `diff.*` cannot decide what the pin sees.

    It is handed to `_porcelain_digest(..., env=...)` as well. Sanitising only the
    fixture's own commands was the first version's mistake: the measured calls go
    through `_git`, which spawned git with **no** `env=` — so the ambient config
    still applied to exactly the calls under test (#4203, and the reason
    `test_measured_git_calls_ignore_the_runners_ambient_config` exists).
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    })
    return env


def _real_git_repo(
    tmp_path: Path, tracked: dict[str, str]
) -> tuple[Path, dict[str, str]]:
    """A throwaway git repo with `tracked` committed — for REAL porcelain.

    Returns `(root, env)`. The two `#4540` tests must not stub `_git`: a
    hand-written porcelain line is exactly how #4540 shipped green. `env` is the
    sanitised environment from `_git_env`; the caller passes it to every MEASURED
    call (`_porcelain_digest(..., env=env)`) as well as to the `git` commands
    below — so the fixture and the code under test see the SAME git.
    """
    root = tmp_path / "repo"
    root.mkdir()
    env = _git_env()

    def run(*a: str) -> None:
        subprocess.run(
            ["git", *a], cwd=str(root), capture_output=True, text=True,
            check=True, env=env,
        )

    run("init", "-q", "-b", "main")
    run("config", "user.email", "test@example.invalid")
    run("config", "user.name", "test")
    for name, content in tracked.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    run("add", "--", *tracked)
    run("commit", "-qm", "init")
    return root, env


# ── #4203: the record must not live in the measured tree (#4572 option (a)) ──
#
# The exclusion that used to hide `--record-out` from the pin was re-derived five
# times and over-matched every time (substring; symlink resolution; glob; `X`→`X/…`
# expansion; lexical-vs-kernel `link/../out`), each spelling a way for a genuinely
# dirty tree to read clean (post_review_dirty False, exit 0 — #4540's class). The
# owner ruled that the class is REMOVED, not guarded: the receipt must be outside
# the measured tree. The pin now performs no path-based exclusion at all, so there
# is nothing whose exactness has to be proven.


def test_record_out_defaults_to_ledger_record_json():
    """M46/D6: an omitted --record-out defaults under LEDGER_ROOT, outside every measured tree.

    The doc declared this test and cited it as coverage; it did not exist (the #4203 class).
    The default must also never land inside the repo, or the tool's own receipt would become
    part of the dirt it measures.
    """
    default = ee._default_record_out()
    assert default.name == "record.json"
    assert default.parent.name == "pi-embedded-evidence"
    assert not default.is_relative_to(ee.REPO_ROOT), default


def test_in_tree_record_out_is_refused(tmp_path, monkeypatch, capsys):
    """An in-tree `--record-out` is a usage error: exit 2, and NO record written.

    This drives the PUBLIC entry point (`main`), so the path resolution the tool
    actually uses is the one under test, with an ABSOLUTE in-tree path so the
    assertion does not depend on the pytest cwd. The load ceiling is forced high so
    the ONLY exit-2 route is the refusal — otherwise a loaded host's `environment
    error` would satisfy the exit code and the test would prove nothing (a previous
    cycle caught exactly this vacuity).
    """
    monkeypatch.setattr(ee, "load1", lambda: 1e9)
    in_tree = ee.REPO_ROOT / "docs" / "evidence" / "3827-green.json"
    before = in_tree.exists()

    rc = ee.main([
        "run", "--selection", "family", "--n", "2",
        "--record-out", str(in_tree),
    ])
    err = capsys.readouterr().err

    assert rc == 2, "an in-tree --record-out must be a usage error"
    assert in_tree.exists() is before, "a refused invocation must write NO record"
    assert str(in_tree) in err, "the message must name the offending path"
    assert "inside the measured tree" in err, "the message must name what is wrong"
    assert "usage error" in err, "it must be the USAGE error, not a load-ceiling error"
    assert "copy or upload" in err, "the message must say what to do instead"
    assert str(ee._default_record_out()) in err, "the message must name the default"

    # The default itself is ACCEPTED — the refusal rejects an in-tree receipt, not
    # records in general. This is the test's own falsifier: a refusal that rejected
    # every --record-out, including the default, would still satisfy the asserts
    # above.
    ee._refuse_in_tree_record_out(ee._default_record_out(), ee.REPO_ROOT)


@pytest.mark.parametrize("relative", [False, True])
def test_link_dotdot_record_out_is_refused(tmp_path, monkeypatch, capsys, relative):
    """The bypass that killed the `abspath` prediction: `lnk/../x.json`.

    `abspath` collapses the `..` LEXICALLY, before resolving `lnk`, so the guard
    read "outside" while `os.replace` resolved `lnk` FIRST and only then applied
    `..`, landing the record at `<tree>/x.json`. Both spellings are covered — an
    ABSOLUTE path with the `..` inline, and a RELATIVE one with the cwd outside the
    repo. Driven through `main` (exit code + message), with the ceiling forced high
    so the only exit-2 route is the refusal.
    """
    monkeypatch.setattr(ee, "load1", lambda: 1e9)
    docs = ee.REPO_ROOT / "docs"
    assert docs.is_dir(), "the fixture needs a real in-tree symlink target"
    workdir = tmp_path / "attack"
    workdir.mkdir()
    (workdir / "lnk").symlink_to(docs, target_is_directory=True)
    name = "3827-link-dotdot.json"
    in_tree = ee.REPO_ROOT / name
    assert not in_tree.exists()

    if relative:
        monkeypatch.chdir(workdir)
        attack = Path("lnk") / ".." / name
    else:
        attack = workdir / "lnk" / ".." / name

    # The kernel truth the prediction must model: the write lands IN the tree. A
    # guard that still collapsed `..` lexically would put this under `workdir`.
    assert ee._written_location(attack) == Path(os.path.realpath(str(ee.REPO_ROOT))) / name, \
        "the guard must resolve lnk BEFORE applying .. — this is the whole bypass"

    rc = ee.main([
        "run", "--selection", "family", "--n", "2",
        "--record-out", str(attack),
    ])
    err = capsys.readouterr().err

    assert rc == 2
    assert "usage error" in err, "it must be the USAGE error, not a load-ceiling error"
    assert "inside the measured tree" in err, f"not the refusal: {err!r}"
    assert not in_tree.exists(), "no in-tree file may appear (filesystem, not just rc)"


def test_the_kernel_follows_the_link_then_applies_dotdot(tmp_path):
    """The premise the guard must model, pinned on the REAL filesystem (in tmp).

    This asserts the KERNEL behaviour that made `abspath` wrong: the write goes
    beside the link TARGET's parent, and NOT beside the directory in which the `..`
    is spelled. It is deliberately safe — the whole demonstration happens under
    `tmp_path` — so a red run cannot dirty the checkout.
    """
    measured = tmp_path / "measured"
    measured.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    (base / "lnk").symlink_to(measured, target_is_directory=True)
    attack = base / "lnk" / ".." / "x.json"

    assert ee._written_location(attack) == tmp_path / "x.json", \
        "the prediction must match where the kernel writes"

    ee._write_record({"k": 1}, attack)
    assert (tmp_path / "x.json").is_file(), "the kernel lands it beside the link target"
    assert not (base / "x.json").exists(), "abspath's lexical prediction is wrong"


def _refused(path, *roots, cwd=None, monkeypatch=None):
    if cwd is not None:
        monkeypatch.chdir(cwd)
    try:
        ee._refuse_in_tree_record_out(path, *roots)
    except ee.UsageError:
        return True
    return False


def test_record_out_containment_matrix(tmp_path, monkeypatch):
    """Every in-tree spelling is refused; every out-of-tree one is not.

    Containment is decided by IDENTITY (the kernel's `samefile`), not by string
    containment, so it is insensitive to both case and lexical divergence. The two
    symlink rows are the ones a string test gets backwards in both directions: a
    link INSIDE the root pointing OUT does not put the file in the root (the link
    is followed), and a link OUTSIDE pointing IN does.
    """
    root = tmp_path / "measured"
    (root / "docs" / "evidence").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    # A symlink INSIDE the measured root that points OUTSIDE it.
    (root / "escape").symlink_to(outside, target_is_directory=True)
    # A symlink OUTSIDE the measured root that points INSIDE it.
    (outside / "enter").symlink_to(root / "docs", target_is_directory=True)
    try:
        case_insensitive = os.path.samefile(root, tmp_path / "MEASURED")
    except OSError:
        case_insensitive = False

    must_refuse = [
        ("plain absolute", root / "docs" / "evidence" / "x.json"),
        ("the root itself", root),
        ("a directory in the root", root / "docs"),
        ("trailing slash", str(root / "docs") + "/"),
        ("link then dotdot", outside / "enter" / ".." / "x.json"),
        ("through a link into the root", outside / "enter" / "x.json"),
    ]
    if case_insensitive:
        must_refuse.append(("case-variant root", tmp_path / "MEASURED" / "x.json"))
    else:
        # Case-SENSITIVE filesystem: `MEASURED` is genuinely a different tree, so
        # refusing it would be a false positive. Identity is what the check keys on.
        assert not _refused(tmp_path / "MEASURED" / "x.json", root, monkeypatch=monkeypatch)

    for label, path in must_refuse:
        assert _refused(path, root, monkeypatch=monkeypatch), f"not refused: {label} -> {path}"

    # Relative spellings, resolved against a cwd that IS the measured tree.
    assert _refused(Path("docs") / "x.json", root, cwd=root, monkeypatch=monkeypatch)
    assert _refused(Path("."), root, cwd=root, monkeypatch=monkeypatch)

    must_accept = [
        ("outside", outside / "x.json"),
        ("inside->outside link", root / "escape" / "x.json"),
        ("space in name", outside / "a b" / "x.json"),
    ]
    for label, path in must_accept:
        assert not _refused(path, root, monkeypatch=monkeypatch), \
            f"wrongly refused: {label} -> {path}"


def test_post_write_check_deletes_an_in_tree_record(tmp_path, monkeypatch, capsys):
    """The fail-closed cross-check: a record past the PRE-write prediction is
    observed after the write and DELETED (#4203).

    The pre-write refusal is monkeypatched to a no-op — the point is the POST-write
    fact check, which must hold even when the prediction is wrong. On the pre-fix
    code the record is simply written and `main` returns the record's own exit code,
    so this test's `rc == 2` and `not target.exists()` both FAIL without the fix.
    The whole case lives under `tmp_path`: a red run cannot dirty the checkout.
    """
    measured = tmp_path / "measured"
    measured.mkdir()
    target = measured / "sub" / "rec.json"

    monkeypatch.setattr(ee, "_refuse_in_tree_record_out", lambda *a, **k: None)
    monkeypatch.setattr(ee, "_build_record", lambda args: {
        "schema": "embedded-evidence/1",
        "pin": {"measured_root": str(measured)},
        "red": {"cause": None},
        "load": {"red_band": None},
        "verdict": {"status": "ALL-GREEN", "closes_issue": False, "violations": []},
        "exit_code": 3,
    })

    rc = ee.main([
        "run", "--selection", "family", "--n", "2",
        "--record-out", str(target),
    ])
    err = capsys.readouterr().err

    assert rc == 2, "an in-tree record must be a usage error, not the record's exit code"
    assert not target.exists(), "the post-write check must DELETE the in-tree record"
    assert not target.parent.exists() or not list(target.parent.iterdir()), \
        "no residue (temp file) may survive the deletion"
    assert "inside the measured tree" in err, "the error must name what was wrong"
    assert "deleted" in err, "the error must say the record was deleted"


def test_post_write_check_guards_repo_root_too(tmp_path, monkeypatch, capsys):
    """The post-write check covers `REPO_ROOT`, not only the measured tree.

    `pin.post_review_dirty` is measured on the INVOKING checkout even when `--ref`
    measures a detached worktree, so a record written into `REPO_ROOT` dirties the
    pin whether or not `--ref` was given. `REPO_ROOT` is redirected to a throwaway
    tree so the test cannot dirty the real checkout.
    """
    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    measured = tmp_path / "measured"          # NOT inside `fake_repo`
    measured.mkdir()
    target = fake_repo / "rec.json"

    monkeypatch.setattr(ee, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(ee, "_refuse_in_tree_record_out", lambda *a, **k: None)
    monkeypatch.setattr(ee, "_build_record", lambda args: {
        "pin": {"measured_root": str(measured)},
        "red": {"cause": None},
        "load": {"red_band": None},
        "verdict": {"status": "ALL-GREEN", "closes_issue": False, "violations": []},
        "exit_code": 3,
    })

    rc = ee.main([
        "run", "--selection", "family", "--n", "2",
        "--record-out", str(target),
    ])
    err = capsys.readouterr().err

    assert rc == 2, "a record inside REPO_ROOT must be refused"
    assert not target.exists(), "the in-repo record must be deleted"
    assert "inside the measured tree" in err


# ── #4585: a FAILED write must not litter the measured tree, and must refuse ──
#
# `mkstemp` normalises its `dir` argument with `os.path.abspath` — LEXICALLY —
# while `os.replace(tmp, out)` resolves `out` through the KERNEL. With a symlink
# followed by `..` the two disagree about the parent, so the temp file (holding
# the COMPLETE record JSON) could be created INSIDE the measured tree and left
# there when `os.replace` failed — and `main` raised the OSError instead of
# refusing with exit 2. Not a path-prediction bug like #4540/#4572 (the pre-write
# refusal is right) and not a fail-open: a crash-and-litter bug on a write-failure
# path. The fix is threefold: create the temp in the PHYSICAL parent, unlink it on
# failure, and never let a failed write escape `main` as a traceback.


def test_a_failed_record_write_leaves_no_temp_in_the_measured_tree(tmp_path, monkeypatch, capsys):
    """#4585: a failed write leaves NO `.rec-*` residue, and exits 2 with a message.

    The destination kernel-resolves to `<base>/destdir` — OUTSIDE the tree, so the
    pre-write refusal is correctly silent — while the LEXICAL parent collapses to
    `<tree>`. Both halves are asserted: the FILESYSTEM (no `.rec-*` in the tree) and
    the message. `rc == 2` alone is vacuous — the load ceiling also returns 2 — so
    the refusal must be the WRITE refusal, naming the cause and the remedy.
    """
    base = tmp_path / "base"
    tree = base / "tree"
    outside = base / "outside"
    destdir = base / "destdir"          # an existing DIRECTORY: `os.replace` must fail
    for d in (tree, outside, destdir):
        d.mkdir(parents=True)
    (tree / "lnk").symlink_to(outside, target_is_directory=True)
    attack = tree / "lnk" / ".." / "destdir"

    # The premise the fix must respect: the DESTINATION is outside the tree (so the
    # pre-write prediction is right and silent), while the LEXICAL parent is inside
    # it — which is what put the temp in the measured tree on the pre-fix code.
    assert ee._written_location(attack) == Path(os.path.realpath(str(destdir)))
    assert Path(os.path.abspath(os.path.dirname(os.fspath(attack)))) == tree, \
        "mkstemp's lexical parent must be the tree for this case to bite"

    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    monkeypatch.setattr(ee, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(ee, "_build_record", lambda args: {
        "pin": {"measured_root": str(tree)},
        "red": {"cause": None},
        "load": {"red_band": None},
        "verdict": {"status": "ALL-GREEN", "closes_issue": False, "violations": []},
        "exit_code": 3,
    })

    rc = ee.main([
        "run", "--selection", "family", "--n", "2",
        "--record-out", str(attack),
    ])
    err = capsys.readouterr().err

    residue = sorted(p.name for p in tree.iterdir())
    assert residue == ["lnk"], (
        "a failed write must leave no `.rec-*` residue inside the measured tree: "
        f"{residue}"
    )
    assert rc == 2, "a failed write must be a refusal, not an uncaught exception"
    assert "could not write the record" in err, f"not the write refusal: {err!r}"
    assert "NOT written" in err, "the operator must be told the record does not exist"
    assert str(attack) in err, "the message must name the failed destination"
    assert "Is a directory" in err or "Errno 21" in err, "the message must carry the cause"
    assert "outside the measured tree" in err, "the message must say what to do instead"
    assert destdir.is_dir(), "the refusal must not remove the destination"


def test_the_sweep_removes_rec_residue_an_earlier_write_left_behind(
    tmp_path, monkeypatch, capsys,
):
    """The `main` sweep is the backstop for residue the primary cleanup missed.

    `_write_record` unlinks its own temp, but if THAT unlink fails (or an older
    invocation crashed) the complete record JSON can survive inside a measured
    tree. This plants that residue and makes the write fail, so the sweep is the
    only thing that can remove it — a dead sweep fails this test.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    # A REAL `mkstemp(prefix=".rec-", suffix=".tmp")` name: 8 of `[a-z0-9_]`.
    residue = tree / ".rec-a1b2c3d4.tmp"
    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    monkeypatch.setattr(ee, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(ee, "_build_record", lambda args: {
        "pin": {"measured_root": str(tree)},
        "red": {"cause": None},
        "load": {"red_band": None},
        "verdict": {"status": "ALL-GREEN", "closes_issue": False, "violations": []},
        "exit_code": 3,
    })

    def _plant_then_fail(rec, out):
        residue.write_text("{}\n")
        raise OSError(21, "Is a directory")

    monkeypatch.setattr(ee, "_write_record", _plant_then_fail)

    rc = ee.main([
        "run", "--selection", "family", "--n", "2",
        "--record-out", str(tmp_path / "rec.json"),
    ])
    err = capsys.readouterr().err

    assert rc == 2
    assert not residue.exists(), "the sweep must remove `.rec-*` residue from the tree"
    assert "could not write the record" in err, f"not the write refusal: {err!r}"


def test_the_sweep_covers_the_destinations_physical_parent(tmp_path, monkeypatch, capsys):
    """The sweep must also reach a NESTED in-tree parent, not only the tree root.

    A symlink swapped for a real directory after the pre-write refusal can resolve
    the destination INTO the tree, so the temp is created in a nested directory
    rather than the tree root. `_write_record`'s own unlink covers that, but when
    THAT fails the sweep is the backstop — and a sweep of the measured roots alone
    would only see the top level. The destination's physical parent is therefore
    swept too; without it this test's residue survives.
    """
    tree = tmp_path / "tree"
    nested = tree / "sub"
    nested.mkdir(parents=True)
    residue = nested / ".rec-e5f6a7b8.tmp"
    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    monkeypatch.setattr(ee, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(ee, "_build_record", lambda args: {
        "pin": {"measured_root": str(tree)},
        "red": {"cause": None},
        "load": {"red_band": None},
        "verdict": {"status": "ALL-GREEN", "closes_issue": False, "violations": []},
        "exit_code": 3,
    })

    def _plant_then_fail(rec, out):
        residue.write_text("{}\n")
        raise OSError(21, "Is a directory")

    monkeypatch.setattr(ee, "_write_record", _plant_then_fail)

    rc = ee.main([
        "run", "--selection", "family", "--n", "2",
        "--record-out", str(nested / "rec.json"),
    ])
    err = capsys.readouterr().err

    assert rc == 2
    assert not residue.exists(), (
        "the sweep must reach the destination's physical parent, not only the "
        "measured roots"
    )
    assert "removed 1 temp file(s)" in err, f"the sweep must report what it removed: {err!r}"


def _unlink_fails_first_call(monkeypatch):
    """Make the FIRST `os.unlink` fail, then delegate to the real one.

    Models the precondition #4585's sweep exists for: a BEST-EFFORT unlink that
    failed, so the caller is the only thing that can still remove the bytes. The
    failure is deliberately transient (one call) — a *persistent* unlink failure
    cannot be repaired by any caller, and is reported, not silently absorbed.
    """
    real_unlink = os.unlink
    calls = {"n": 0}

    def _fails_once(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(1, "Operation not permitted")
        return real_unlink(path)

    monkeypatch.setattr(ee.os, "unlink", _fails_once)
    return calls


def test_a_non_oserror_write_failure_still_sweeps_the_temp(tmp_path, monkeypatch, capsys):
    """#4585 (a): cleanup must NOT be gated on the exception TYPE.

    `_write_record`'s own unlink is best-effort, so a failure class that is not
    `OSError` — here `json.dump` raising `TypeError` — combined with an unlink that
    fails leaves the temp (holding partial record bytes) inside the measured tree.
    Pre-fix, `main`'s `except OSError` did not match, so the `TypeError` propagated
    WITHOUT a sweep. Both halves are asserted: the FILESYSTEM, and that the original
    exception still propagates (the sweep must not swallow it). The exit code is
    deliberately NOT asserted — a traceback is the pre-existing non-OSError contract
    and is not what this test pins.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    parent = tree / "sub"          # the destination's physical parent, IN the tree
    parent.mkdir()
    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    monkeypatch.setattr(ee, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(ee, "_build_record", lambda args: {
        "pin": {"measured_root": str(tree)},
        "red": {"cause": None},
        "load": {"red_band": None},
        "verdict": {"status": "ALL-GREEN", "closes_issue": False, "violations": []},
        "exit_code": 3,
    })

    def _raise_type_error(*_a, **_k):
        raise TypeError("simulated json.dump failure")

    monkeypatch.setattr(ee.json, "dump", _raise_type_error)
    _unlink_fails_first_call(monkeypatch)

    with pytest.raises(TypeError):
        ee.main([
            "run", "--selection", "family", "--n", "2",
            "--record-out", str(parent / "rec.json"),
        ])
    capsys.readouterr()

    residue = sorted(p.name for p in parent.iterdir())
    assert residue == [], (
        "a non-OSError write failure must still sweep the temp out of the "
        f"measured tree: {residue}"
    )


def test_an_in_tree_record_survives_a_failed_verify_unlink_and_is_swept(
    tmp_path, monkeypatch, capsys,
):
    """#4585 (b): the `UsageError` path sweeps too, and removes the record itself.

    The record lands INSIDE the tree (the pre-write prediction was fooled), and
    `_verify_record_landed_outside`'s own `os.unlink(out)` fails. Pre-fix, `main`
    printed the usage error and returned 2 with the COMPLETE record JSON still in
    the measured tree. `rc == 2` is identical either way, so the discriminator is
    the FILESYSTEM plus the refusal message.
    """
    tree = tmp_path / "tree"
    parent = tree / "sub"
    parent.mkdir(parents=True)
    out = parent / "rec.json"
    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    monkeypatch.setattr(ee, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(ee, "_build_record", lambda args: {
        "pin": {"measured_root": str(tree)},
        "red": {"cause": None},
        "load": {"red_band": None},
        "verdict": {"status": "ALL-GREEN", "closes_issue": False, "violations": []},
        "exit_code": 3,
    })
    _unlink_fails_first_call(monkeypatch)

    rc = ee.main([
        "run", "--selection", "family", "--n", "2",
        "--record-out", str(out),
    ])
    err = capsys.readouterr().err

    assert rc == 2, "the run must still refuse the in-tree record"
    assert not out.exists(), (
        "the record that landed inside the measured tree must be swept before "
        "the refusal returns"
    )
    assert not list(parent.glob(".rec-*")) and not list(tree.glob(".rec-*")), \
        "no temp residue may survive either"
    assert "usage error" in err, f"the refusal must be reported: {err!r}"
    assert "inside the measured tree" in err, f"not the in-tree refusal: {err!r}"


def test_the_sweep_does_not_delete_a_users_rec_named_file(tmp_path, monkeypatch, capsys):
    """Control: only mkstemp's EXACT shape is swept — a lookalike user file survives.

    `_REC_RESIDUE_RE` is `.rec-` + 8 of `[a-z0-9_]` + `.tmp`, so a user's
    `.rec-userdefined.tmp` (wrong-length random part), `.rec-user.txt` and
    `rec-file.tmp` are untouched, while genuine residue IS removed. The pre-fix
    `.rec-*.tmp` glob deleted the lookalikes — the tool cleaning up a file it never
    created.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    real_residue = tree / ".rec-a1b2c3d4.tmp"      # mkstemp's exact shape
    lookalike = tree / ".rec-userdefined.tmp"     # prefix + suffix, wrong random part
    user_txt = tree / ".rec-user.txt"
    user_plain = tree / "rec-file.tmp"
    for p in (real_residue, lookalike, user_txt, user_plain):
        p.write_text("keep me\n")

    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    monkeypatch.setattr(ee, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(ee, "_build_record", lambda args: {
        "pin": {"measured_root": str(tree)},
        "red": {"cause": None},
        "load": {"red_band": None},
        "verdict": {"status": "ALL-GREEN", "closes_issue": False, "violations": []},
        "exit_code": 3,
    })

    def _fail(rec, out):
        raise OSError(21, "Is a directory")

    monkeypatch.setattr(ee, "_write_record", _fail)

    rc = ee.main([
        "run", "--selection", "family", "--n", "2",
        "--record-out", str(tmp_path / "rec.json"),
    ])
    err = capsys.readouterr().err

    assert rc == 2, f"the write refusal must still fire: {err!r}"
    assert not real_residue.exists(), "genuine mkstemp-shaped residue must be removed"
    assert lookalike.exists(), (
        "a user file matching the old open glob but not mkstemp's shape must survive"
    )
    assert user_txt.exists() and user_plain.exists(), (
        "files outside the residue shape must never be touched"
    )
    assert "removed 1 temp file(s)" in err, f"exactly the real residue: {err!r}"


def test_an_out_of_tree_record_is_written_and_the_run_succeeds(tmp_path, monkeypatch, capsys):
    """The ordinary path is unaffected: an out-of-tree record is written, exit is the record's.

    The fix now resolves the PHYSICAL parent and sweeps `.rec-*`, so this is the
    falsifier for a fix that over-sweeps, wrongly refuses, or deletes a good
    record. `main` must return the record's OWN exit code (0 here) — a refusal
    would return 2 instead.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    out = tmp_path / "records" / "rec.json"
    monkeypatch.setattr(ee, "REPO_ROOT", fake_repo)

    record = {
        "pin": {"measured_root": str(tree)},
        "red": {"cause": None},
        "load": {"red_band": "L-A"},
        "verdict": {"status": "ALL-GREEN", "closes_issue": True, "violations": []},
        "exit_code": 0,
    }
    monkeypatch.setattr(ee, "_build_record", lambda args: record)

    rc = ee.main([
        "run", "--selection", "family", "--n", "2",
        "--record-out", str(out),
    ])
    err = capsys.readouterr().err

    assert rc == 0, f"the ordinary out-of-tree write must succeed: {err!r}"
    assert out.is_file(), "the out-of-tree record must be written"
    assert json.loads(out.read_text()) == record, "the record must round-trip"
    assert not list(out.parent.glob(".rec-*.tmp")), "success must consume the temp"
    assert not list(tree.glob(".rec-*.tmp")), "no residue may appear in the measured tree"


def test_pin_detects_a_genuine_edit_and_ignores_the_out_of_tree_record(tmp_path, monkeypatch):
    """The pin still FIRES on real dirt, and the tool's own record cannot perturb it.

    Real git in a throwaway repo — no stubbing of `_git` / `_porcelain_digest`,
    since the whole defect class came from wrong assumptions about git's behaviour.
    The clean assertion is the falsifier for a vacuous version: if
    `_porcelain_digest` always returned `False`, `clean` would pass but the edit and
    untracked-directory assertions would not.
    """
    root, env = _real_git_repo(tmp_path, {"src/keep.py": "y = 1\n", "README.md": "r\n"})

    clean, clean_dirty = ee._porcelain_digest(root, env=env)
    assert clean_dirty is False, "a fresh checkout must read clean"

    # The tool writes its record to its temp default, outside the measured tree.
    monkeypatch.setattr(ee.tempfile, "tempdir", str(tmp_path / "tmp"))
    out = ee._default_record_out()
    ee._write_record({"schema": "embedded-evidence/1", "verdict": {}}, out)
    assert out.is_file()
    assert not ee._written_location(out).is_relative_to(ee._written_location(root)), (
        "the test's own premise: the record lands OUTSIDE the measured tree"
    )

    after_record, still_dirty = ee._porcelain_digest(root, env=env)
    assert still_dirty is False, "an out-of-tree record must not dirty the pin"
    assert after_record == clean, "an out-of-tree record must not move the digest"

    # A genuine edit in the invoking checkout IS detected ...
    (root / "src" / "keep.py").write_text("y = 2\n")
    edited, dirty_edit = ee._porcelain_digest(root, env=env)
    assert dirty_edit is True
    assert edited != clean, "the digest must change when the tree changes"

    # ... and so is an untracked directory — the shape whose COLLAPSED porcelain
    # entry made the old exclusion a permanent false-FAIL (#4203). `-uall` is what
    # keeps it from collapsing; with no exclusion left it is simply dirt.
    (root / "docs" / "evidence").mkdir(parents=True)
    (root / "docs" / "evidence" / "green.json").write_text("{}\n")
    _, dirty_untracked = ee._porcelain_digest(root, env=env)
    assert dirty_untracked is True


def test_measured_git_calls_ignore_the_runners_ambient_config(tmp_path, monkeypatch):
    """#4203 (C): the sanitised env must reach the MEASURED calls, not just the fixture.

    `_porcelain_digest` → `_git` is where the pin is measured, and `_git` spawns git
    with the AMBIENT environment unless the caller passes one. Sanitising only the
    fixture's own commands left exactly the calls under test exposed, so a runner
    with a global `core.excludesFile` (or `core.autocrlf`, `core.fsmonitor`,
    `status.*`, …) could make these tests assert about a tree git was configured not
    to report — a vacuous pass. Both halves are asserted: the ambient config really
    does bite, and the pinned env defeats it.
    """
    root, env = _real_git_repo(tmp_path, {"src/keep.py": "y = 1\n", "README.md": "r\n"})
    (root / "fresh.json").write_text("{}\n")  # untracked: what an ignore rule hides

    ignores = tmp_path / "ignore-everything"
    ignores.write_text("*\n")
    hostile = tmp_path / "hostile.gitconfig"
    hostile.write_text(f"[core]\n\texcludesFile = {ignores}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)

    # (1) the leak is REAL. A vacuous version of this test would be one where the
    #     hostile config is inert, so this assertion is the test's own falsifier.
    _, leaked_dirty = ee._porcelain_digest(root)
    assert leaked_dirty is False, "the hostile ambient config must actually bite"

    # (2) `_git`'s `env` argument is the only thing between the measured call and a
    #     hostile `GIT_DIR`; with the ambient environment git cannot find the repo.
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "no-such-git-dir"))
    with pytest.raises(RuntimeError):
        ee._git("rev-parse", "--git-dir", cwd=root)
    assert ee._git("rev-parse", "--git-dir", cwd=root, env=env).strip() != ""

    # (3) and it reaches the MEASURED call, not merely a direct `_git` probe.
    _, isolated_dirty = ee._porcelain_digest(root, env=env)
    assert isolated_dirty is True


# ── #4203: every conjunct flips in BOTH directions on a PRODUCED record ──────

class TestConjunctFalsifiability:
    """#4203 — five `closes_issue` conjuncts were non-falsifiable.

    Two could never PASS on a produced record (their fields were hardcoded, so
    the producer could not reach them: `no-rate-change`,
    `certification-not-on-shipping-surface`). Three could never FAIL (their
    inputs were literals: the per-run half of `pin-not-airtight`, and
    `certificate-not-bound-to-review-head`). A conjunct that cannot pass, or
    cannot fail, reads as protection while supplying none.

    Each test below drives `_build_record` — the PRODUCER, not a hand-built
    dict — and mutates exactly the input one conjunct reads, asserting the flip
    in BOTH directions. A test that only fed `closes_issue` a dict would stay
    green on the original defect, where the producer could never emit the
    passing shape.
    """

    @staticmethod
    def _run(files, run_id, bucket):
        red = bucket != "green"
        return {
            "run_id": run_id,
            "bucket": bucket,
            "files": list(files),
            "returncode": 1 if red else 0,
            "step_wall_s": 1.0,
            "observed": len(files),
            "executed": len(files),
            "skipped": 0,
            "load": {"before": 1.0, "after": 1.0, "band": "L-A"},
            "redis_log_cause": "module-fork-eexist" if red else None,
            "cause_evidence": {},
            "timed_out": bucket == "timeout-red",
            # The independent side `same_file_list` reads is a junit OBSERVATION.
            "observed_files": ee._JunitObservation(
                tuple(files), tuple([files[0]]) if red else (), f"junit-{run_id}.xml"
            ),
        }

    @staticmethod
    def _produce(
        monkeypatch,
        tmp_path,
        *,
        runs,
        pairing_ref=None,
        baseline=None,
        measured_digests=None,
        review_digest=("sha256:review-clean", False),
        checkout_head="a" * 40,
        measured_commit="b" * 40,
        ancestor_rc=0,
        **arg_over,
    ):
        import argparse

        measured_path = tmp_path / "worktree"
        pair_path = tmp_path / "pairing-worktree"

        # No real worktree, no real git, no real subprocess: the test drives the
        # producer's LOGIC. `_worktree_at` is the one seam that materializes a ref.
        monkeypatch.setattr(
            ee, "_worktree_at",
            lambda ref, run_root, name: (tmp_path / name, True),
        )

        class _FakeGitProc:
            returncode = 0

        def _fake_run(cmd, *a, **k):
            proc = _FakeGitProc()
            # `_strict_ancestor` asks git a yes/no question through the rc of
            # `merge-base --is-ancestor`; the test controls the answer.
            if (isinstance(cmd, list) and len(cmd) >= 2
                    and cmd[0] == "git" and cmd[1] == "merge-base"):
                proc.returncode = ancestor_rc
            return proc

        monkeypatch.setattr(ee.subprocess, "run", _fake_run)
        monkeypatch.setattr(
            ee, "_manifest_receipt",
            lambda files, marker, out_dir: {
                "path": "stub",
                "digest": "sha256:0",
                "count": len(files),
                "unique_count": len(files),
                "marker": marker,
            },
        )

        def _run_once(files, root, run_root, run_id, marker, timeout):
            if Path(root) == pair_path:
                assert baseline is not None, "pairing ref declared without a baseline run"
                return baseline
            return runs[run_id - 1]

        monkeypatch.setattr(ee, "_run_once", _run_once)
        monkeypatch.setattr(ee, "load1", lambda: 1.0)
        monkeypatch.setattr(ee, "_tool_version", lambda: "blob0")

        digests = list(measured_digests or [("sha256:tree-stable", False)] * (len(runs) + 1))

        def _porcelain_digest(cwd, env=None):
            if Path(cwd) == measured_path:
                return digests.pop(0)
            return review_digest

        monkeypatch.setattr(ee, "_porcelain_digest", _porcelain_digest)

        def _git(*a, cwd=None):
            if a == ("rev-parse", "HEAD"):
                # The invoking checkout's HEAD vs the worktree's measured commit —
                # two independent reads, which is the whole point of the conjunct.
                return checkout_head if cwd is None else measured_commit
            if a[0] == "rev-parse" and a[1].endswith("^{tree}"):
                return "c" * 40
            return "d" * 40

        monkeypatch.setattr(ee, "_git", _git)

        args = argparse.Namespace(
            selection="family",
            n=len(runs),
            ref="deadbeef",
            pairing_ref=pairing_ref,
            marker=ee.DEFAULT_MARKER,
            load_ceiling=1e9,
            run_timeout=1,
            record_role="historical-attestation",
            record_out=None,
            cmd="run",
            environment_error=None,
            surface=None,
            surface_assertion=None,
        )
        for k, v in arg_over.items():
            setattr(args, k, v)
        return ee._build_record(args)

    def test_tree_move_between_runs_sets_tree_moved(self, monkeypatch, tmp_path):
        """`pin-not-airtight`'s per-run half: PASS on a stable tree, FAIL on a move."""
        files = list(ee.FAMILY_REPRODUCERS)
        runs = [self._run(files, 1, "green"), self._run(files, 2, "green")]

        # The digest is read once BEFORE run 1 and once after each run.
        rec = self._produce(
            monkeypatch, tmp_path, runs=runs,
            measured_digests=[("sha256:tree-a", False)] * 3,
        )
        assert [r["tree_moved"] for r in rec["runs"]] == [False, False]
        assert "pin-not-airtight" not in ee.closes_issue(rec)[1]

        # MUTATION: an edit between run 1 and run 2 moves the measured tree.
        rec2 = self._produce(
            monkeypatch, tmp_path, runs=runs,
            measured_digests=[("sha256:tree-a", False), ("sha256:tree-a", False),
                              ("sha256:tree-b", False)],
        )
        assert [r["tree_moved"] for r in rec2["runs"]] == [False, True]
        ok2, reasons2 = ee.closes_issue(rec2)
        assert ok2 is False
        assert "pin-not-airtight" in reasons2

    def test_tree_move_during_run_one_sets_tree_moved(self, monkeypatch, tmp_path):
        """D11: a tree that moves only DURING run 1 is caught.

        The baseline digest used to be appended AFTER run 1, so run 1 compared with
        itself — `tree_moved` was False for run 1 by construction and this move was
        invisible.
        """
        files = list(ee.FAMILY_REPRODUCERS)
        runs = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        rec = self._produce(
            monkeypatch, tmp_path, runs=runs,
            measured_digests=[("sha256:tree-a", False), ("sha256:tree-b", False),
                              ("sha256:tree-b", False)],
        )
        assert [r["tree_moved"] for r in rec["runs"]] == [True, True]
        ok, reasons = ee.closes_issue(rec)
        assert ok is False
        assert "pin-not-airtight" in reasons

    def test_load_bands_must_overlap(self, monkeypatch, tmp_path):
        """F17/D14 (threat row 10): a red at L-C and greens at L-A must NOT close.

        `bands` was built from the measured runs only, so on a closing shape it held
        green bands alone and `len(bands) == 1` was trivially satisfied.
        """
        files = list(ee.FAMILY_REPRODUCERS)
        green = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        for r in green:
            r["load"]["band"] = ee.load_band(3.0)          # L-A
        baseline_red = self._run(files, 1, "unexpected-divergence")
        baseline_red["load"]["band"] = ee.load_band(30.0)  # L-C
        same = "a" * 40

        rec = self._produce(
            monkeypatch, tmp_path, runs=green,
            pairing_ref="pairref", baseline=baseline_red,
            record_role="closing", ancestor_rc=0,
            checkout_head=same, measured_commit=same,
            surface="tortoise_search",
            surface_assertion="tests/test_x.py::test_consumer_surface",
        )
        assert rec["load"]["overlap"] is False
        assert rec["load"]["red_band"] == "L-C"
        assert rec["load"]["declared_band"] == "L-C"
        ok, reasons = ee.closes_issue(rec)
        assert ok is False
        assert "load-bands-do-not-overlap" in reasons
        # Threat row 10 / D14: a non-overlap is a red, never a pass.
        assert ee.exit_code(rec) == 1

        # POSITIVE: both halves in ONE band still closes.
        baseline_red2 = self._run(files, 1, "unexpected-divergence")
        baseline_red2["load"]["band"] = ee.load_band(30.0)
        for r in green:
            r["load"]["band"] = ee.load_band(30.0)
        rec2 = self._produce(
            monkeypatch, tmp_path, runs=green,
            pairing_ref="pairref", baseline=baseline_red2,
            record_role="closing", ancestor_rc=0,
            checkout_head=same, measured_commit=same,
            surface="tortoise_search",
            surface_assertion="tests/test_x.py::test_consumer_surface",
        )
        assert rec2["load"]["overlap"] is True
        assert "load-bands-do-not-overlap" not in ee.closes_issue(rec2)[1]

    def test_red_green_mix_is_derived_from_real_runs(self, monkeypatch, tmp_path):
        """F17/schema: `red_green_mix` counts the runs that entered the comparison."""
        files = list(ee.FAMILY_REPRODUCERS)
        green = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        baseline_red = self._run(files, 1, "unexpected-divergence")
        rec = self._produce(
            monkeypatch, tmp_path, runs=green,
            pairing_ref="pairref", baseline=baseline_red,
            record_role="closing", ancestor_rc=0,
        )
        # Two measured greens + the attested baseline red; never the `{"red": 1,
        # "green": 0}` literal that contradicted `runs == ['green','green']`.
        assert rec["red"]["red_green_mix"] == {"red": 1, "green": 2}
        assert sum(rec["red"]["red_green_mix"].values()) == len(rec["runs"]) + 1

    def test_attempted_is_not_a_constant(self, monkeypatch, tmp_path):
        """The dropped AND-term: `bool(runs)` was True on every reachable record."""
        files = list(ee.FAMILY_REPRODUCERS)
        green = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        no_red = self._produce(
            monkeypatch, tmp_path, runs=green,
            record_role="historical-attestation",
        )
        assert no_red["red"]["at_fixed_commit"]["attempted"] is False
        # ... and the conjunct does not depend on it: the rate-change claim is
        # decided by `rate_change` / `appeared`.
        probe = _nonclosing_record()
        probe["red"]["at_fixed_commit"]["attempted"] = False
        probe["red"]["at_fixed_commit"]["rate_change"] = True
        probe["red"]["at_fixed_commit"]["appeared"] = False
        assert "no-rate-change" not in ee.closes_issue(probe)[1]

    def test_verdict_status_follows_the_attested_red(self, monkeypatch, tmp_path):
        """`status`/`green_only` must follow `red.cause`, not the measured runs."""
        files = list(ee.FAMILY_REPRODUCERS)
        green = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        # A pairing-ref red that does NOT close (internal seam surface): the old code
        # printed ALL-GREEN / green_only: true while `red.cause` was non-null.
        rec = self._produce(
            monkeypatch, tmp_path, runs=green,
            pairing_ref="pairref",
            baseline=self._run(files, 1, "unexpected-divergence"),
            record_role="closing",
            surface="TortoiseSDK.search",
            surface_assertion="tests/test_x.py::test_internal",
        )
        assert rec["red"]["cause"] is not None
        assert rec["verdict"]["green_only"] is False
        assert rec["verdict"]["status"] == "RED-AT-PAIRING-REF"
        assert rec["verdict"]["status"] != "ALL-GREEN"

    def test_baseline_run_is_persisted(self, monkeypatch, tmp_path):
        """A verifier must be able to re-derive `rate_change` from the record."""
        files = list(ee.FAMILY_REPRODUCERS)
        green = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        baseline_red = self._run(files, 1, "unexpected-divergence")
        rec = self._produce(
            monkeypatch, tmp_path, runs=green,
            pairing_ref="pairref", baseline=baseline_red,
            record_role="closing",
        )
        base = rec["red"]["baseline_run"]
        assert base is not None
        appeared = any(r["bucket"] in ee.BUCKETS_RED for r in rec["runs"])
        assert (base["bucket"] in ee.BUCKETS_RED) and not appeared
        # It must survive the JSON round-trip the record file performs.
        round_tripped = json.loads(json.dumps(ee._jsonable(rec)))
        assert round_tripped["red"]["baseline_run"]["bucket"] in ee.BUCKETS_RED

    def test_baseline_tree_move_fails_the_pin(self, monkeypatch, tmp_path):
        """D11: the attested baseline's own tree state is part of the pin."""
        files = list(ee.FAMILY_REPRODUCERS)
        green = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        rec = self._produce(
            monkeypatch, tmp_path, runs=green,
            pairing_ref="pairref",
            baseline=self._run(files, 1, "unexpected-divergence"),
            record_role="closing",
        )
        assert "pin-not-airtight" not in ee.closes_issue(rec)[1]
        rec["red"]["baseline_run"]["tree_moved"] = True
        assert "pin-not-airtight" in ee.closes_issue(rec)[1]

    def test_certificate_is_bound_to_head_sha(self, monkeypatch, tmp_path):
        """`certificate-not-bound-to-review-head`: the head is read independently."""
        files = list(ee.FAMILY_REPRODUCERS)
        runs = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        same = "a" * 40

        rec = self._produce(
            monkeypatch, tmp_path, runs=runs,
            checkout_head=same, measured_commit=same,
        )
        assert rec["pin"]["head_sha"] == rec["pin"]["commit"]
        assert "certificate-not-bound-to-review-head" not in ee.closes_issue(rec)[1]

        # MUTATION: the branch moved past the measured commit (a post-review edit).
        rec2 = self._produce(
            monkeypatch, tmp_path, runs=runs,
            checkout_head="e" * 40, measured_commit=same,
        )
        assert rec2["pin"]["head_sha"] != rec2["pin"]["commit"]
        ok2, reasons2 = ee.closes_issue(rec2)
        assert ok2 is False
        assert "certificate-not-bound-to-review-head" in reasons2

    def test_certificate_invalidated_by_post_review_edit(self, monkeypatch, tmp_path):
        """A dirty reviewing checkout invalidates the head binding."""
        files = list(ee.FAMILY_REPRODUCERS)
        runs = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        same = "a" * 40

        rec = self._produce(
            monkeypatch, tmp_path, runs=runs,
            checkout_head=same, measured_commit=same,
            review_digest=("sha256:review-clean", False),
        )
        assert rec["pin"]["post_review_dirty"] is False
        assert "certificate-not-bound-to-review-head" not in ee.closes_issue(rec)[1]

        # MUTATION: an uncommitted post-review edit lands in the checkout.
        rec2 = self._produce(
            monkeypatch, tmp_path, runs=runs,
            checkout_head=same, measured_commit=same,
            review_digest=("sha256:review-dirty", True),
        )
        assert rec2["pin"]["post_review_dirty"] is True
        ok2, reasons2 = ee.closes_issue(rec2)
        assert ok2 is False
        assert "certificate-not-bound-to-review-head" in reasons2

    def test_certification_binds_to_the_shipping_surface(self, monkeypatch, tmp_path):
        """The producer can reach a PASS; an internal seam or empty assertion fails."""
        files = list(ee.FAMILY_REPRODUCERS)
        runs = [self._run(files, 1, "green"), self._run(files, 2, "green")]

        rec = self._produce(
            monkeypatch, tmp_path, runs=runs,
            surface="tortoise_search",
            surface_assertion="tests/test_x.py::test_consumer_surface",
        )
        assert rec["red"]["at_fixed_commit"]["surface"] == "tortoise_search"
        assert "certification-not-on-shipping-surface" not in ee.closes_issue(rec)[1]

        # MUTATION 1: the proof is asserted against an INTERNAL helper.
        rec2 = self._produce(
            monkeypatch, tmp_path, runs=runs,
            surface="TortoiseSDK.search",
            surface_assertion="tests/test_x.py::test_internal",
        )
        ok2, reasons2 = ee.closes_issue(rec2)
        assert ok2 is False
        assert "certification-not-on-shipping-surface" in reasons2

        # MUTATION 2: a member surface with no resolving test-ID.
        rec3 = self._produce(
            monkeypatch, tmp_path, runs=runs,
            surface="tortoise_recall",
            surface_assertion="",
        )
        ok3, reasons3 = ee.closes_issue(rec3)
        assert ok3 is False
        assert "certification-not-on-shipping-surface" in reasons3

    def test_shipping_surfaces_are_declared(self, monkeypatch, tmp_path):
        """The constant is the two agent-facing names; the producer RECORDS it,
        and the conjunct CONSUMES it rather than a private copy."""
        assert ee.SHIPPING_SURFACES == ("tortoise_search", "tortoise_recall")

        # The producer must carry the CALLER-declared surface into the record.
        # (On the pre-#4203 tool the field was a `None` literal and this fails.)
        files = list(ee.FAMILY_REPRODUCERS)
        runs = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        rec = self._produce(
            monkeypatch, tmp_path, runs=runs,
            surface="tortoise_search",
            surface_assertion="tests/test_x.py::test_consumer_surface",
        )
        assert rec["red"]["at_fixed_commit"]["surface"] == "tortoise_search"
        assert rec["red"]["at_fixed_commit"]["surface_assertion"] == (
            "tests/test_x.py::test_consumer_surface"
        )

        rec2 = _nonclosing_record()
        rec2["red"]["at_fixed_commit"]["surface"] = "tortoise_search"
        rec2["red"]["at_fixed_commit"]["surface_assertion"] = "tests/x.py::test_y"
        assert "certification-not-on-shipping-surface" not in ee.closes_issue(rec2)[1]
        # MUTATION: empty the constant — the same record must now fail. This proves
        # the conjunct consumes the constant rather than a private copy.
        monkeypatch.setattr(ee, "SHIPPING_SURFACES", ())
        assert "certification-not-on-shipping-surface" in ee.closes_issue(rec2)[1]

    def test_at_fixed_commit_rate_change_required(self, monkeypatch, tmp_path):
        """`no-rate-change`: reachable PASS on a real rate change, fails otherwise."""
        files = list(ee.FAMILY_REPRODUCERS)
        green = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        baseline_red = self._run(files, 1, "unexpected-divergence")

        rec = self._produce(
            monkeypatch, tmp_path, runs=green,
            pairing_ref="pairref", baseline=baseline_red,
        )
        assert rec["red"]["at_fixed_commit"] == {
            "attempted": True,
            "appeared": False,
            "rate_change": True,
            "mutation": None,
            "mutation_operator": None,
            "mutation_target_is_fix_branch": False,
            "mutation_red_returned": False,
            "surface": None,
            "surface_assertion": None,
        }
        assert "no-rate-change" not in ee.closes_issue(rec)[1]

        # MUTATION 1: the "fix" changed nothing — the red still appears at the
        # measured (fixed) commit.
        red = [
            self._run(files, 1, "unexpected-divergence"),
            self._run(files, 2, "unexpected-divergence"),
        ]
        rec2 = self._produce(
            monkeypatch, tmp_path, runs=red,
            pairing_ref="pairref", baseline=baseline_red,
        )
        assert rec2["red"]["at_fixed_commit"]["appeared"] is True
        assert rec2["red"]["at_fixed_commit"]["rate_change"] is False
        assert "no-rate-change" in ee.closes_issue(rec2)[1]

        # MUTATION 2: the pairing ref is GREEN for the selection — there is no red
        # to have changed, so no rate change can be claimed.
        rec3 = self._produce(
            monkeypatch, tmp_path, runs=green,
            pairing_ref="pairref", baseline=self._run(files, 1, "green"),
        )
        assert rec3["red"]["at_fixed_commit"]["rate_change"] is False
        assert "no-rate-change" in ee.closes_issue(rec3)[1]

    def test_pairing_ref_must_be_strict_ancestor(self, monkeypatch, tmp_path):
        """C1/D16: equal, descendant or unrelated pairing refs are usage errors.

        `--pairing-ref` drives `rate_change`, so without the ancestry check a red
        measured at ANY ref made `no-rate-change` satisfiable and the record's
        central claim (the red appeared BEFORE the fix) rested on an arbitrary sha.
        """
        files = list(ee.FAMILY_REPRODUCERS)
        green = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        baseline_red = self._run(files, 1, "unexpected-divergence")

        # POSITIVE: a strict ancestor is accepted and labelled from the ancestry.
        rec = self._produce(
            monkeypatch, tmp_path, runs=green,
            pairing_ref="pairref", baseline=baseline_red,
            record_role="closing", ancestor_rc=0,
        )
        assert rec["pin"]["pairing_ref"] is not None
        assert rec["red"]["ref_role"] == "last-before-first-family-fix"

        # EQUAL: the pairing ref IS the measured commit (no `merge-base` call).
        with pytest.raises(ee.UsageError):
            self._produce(
                monkeypatch, tmp_path, runs=green,
                pairing_ref="pairref", baseline=baseline_red,
                record_role="closing", ancestor_rc=0,
                measured_commit="d" * 40,
            )

        # DESCENDANT / UNRELATED: `merge-base --is-ancestor` answers rc 1.
        with pytest.raises(ee.UsageError):
            self._produce(
                monkeypatch, tmp_path, runs=green,
                pairing_ref="pairref", baseline=baseline_red,
                record_role="closing", ancestor_rc=1,
            )

    def test_ref_role_is_derived_not_declared(self, monkeypatch, tmp_path):
        files = list(ee.FAMILY_REPRODUCERS)
        green = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        no_pair = self._produce(
            monkeypatch, tmp_path, runs=green,
            record_role="historical-attestation",
        )
        assert no_pair["red"]["ref_role"] == "pinned-head-pre-fix"
        with_pair = self._produce(
            monkeypatch, tmp_path, runs=green,
            pairing_ref="pairref",
            baseline=self._run(files, 1, "unexpected-divergence"),
            record_role="closing",
        )
        assert with_pair["red"]["ref_role"] == "last-before-first-family-fix"

    def test_record_role_closing_requires_same_invocation_pair(self, monkeypatch, tmp_path):
        """M52/C1: `closing` with no `--pairing-ref` is a usage error, not a record."""
        files = list(ee.FAMILY_REPRODUCERS)
        green = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        with pytest.raises(ee.UsageError):
            self._produce(monkeypatch, tmp_path, runs=green, record_role="closing")
        # POSITIVE: with a same-invocation pair it is accepted.
        rec = self._produce(
            monkeypatch, tmp_path, runs=green,
            pairing_ref="pairref",
            baseline=self._run(files, 1, "unexpected-divergence"),
            record_role="closing",
        )
        assert rec["record_role"] == "closing"

    def test_closing_role_without_pairing_ref_writes_no_record(self, tmp_path, capsys):
        """M52/C1: the documented usage error is exit 2, with NO record written."""
        out = tmp_path / "rec.json"
        rc = ee.main(["run", "--record-role", "closing", "--record-out", str(out)])
        assert rc == 2
        assert not out.exists()
        # It must be the USAGE error, not an environment error — otherwise the test
        # would pass on a host where the real work failed before the precondition.
        assert "usage error" in capsys.readouterr().err

    def test_internal_seam_only_mutation_is_non_closing(self, monkeypatch, tmp_path):
        """Plan R1: an internal-helper-only proof is non-closing (exit 1)."""
        files = list(ee.FAMILY_REPRODUCERS)
        runs = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        rec = self._produce(
            monkeypatch, tmp_path, runs=runs,
            surface="TortoiseSDK.search",
            surface_assertion="tests/test_x.py::test_internal",
        )
        assert "certification-not-on-shipping-surface" in ee.closes_issue(rec)[1]

    def test_producer_can_emit_a_closing_record(self, monkeypatch, tmp_path):
        """The headline defect: `closes_issue` was False for EVERY produced record.

        A legitimately-good GREEN-half shape — a paired red at the pairing ref, an
        all-green fixed commit at the same HEAD as the reviewing checkout, a clean
        tree, and a declared shipping surface — must now reach a CLOSING verdict.
        This is the end-to-end PASS that the hardcoded `at_fixed_commit` /
        `surface` fields made impossible, and it exercises all thirteen conjuncts
        at once.
        """
        files = list(ee.FAMILY_REPRODUCERS)
        green = [self._run(files, 1, "green"), self._run(files, 2, "green")]
        same = "a" * 40
        rec = self._produce(
            monkeypatch, tmp_path,
            runs=green,
            pairing_ref="pairref",
            baseline=self._run(files, 1, "unexpected-divergence"),
            record_role="closing",
            checkout_head=same,
            measured_commit=same,
            surface="tortoise_search",
            surface_assertion="tests/test_x.py::test_consumer_surface",
        )
        ok, reasons = ee.closes_issue(rec)
        assert ok is True, f"producer cannot reach a closing record: {reasons}"
        assert reasons == []
        assert ee.exit_code(rec) == 0
        assert rec["verdict"]["status"] == "PAIRED-RED-DEMONSTRATED"
