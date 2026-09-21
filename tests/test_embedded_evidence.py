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
from pathlib import Path
from typing import ClassVar

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
        monkeypatch.setattr(ee.subprocess, "run", lambda *a, **k: None)
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

        digests = list(measured_digests or [("sha256:tree-stable", False)] * len(runs))

        def _porcelain_digest(cwd, exclude=None):
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
            record_role="closing",
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

        rec = self._produce(
            monkeypatch, tmp_path, runs=runs,
            measured_digests=[("sha256:tree-a", False), ("sha256:tree-a", False)],
        )
        assert [r["tree_moved"] for r in rec["runs"]] == [False, False]
        assert "pin-not-airtight" not in ee.closes_issue(rec)[1]

        # MUTATION: an edit between run 1 and run 2 moves the measured tree.
        rec2 = self._produce(
            monkeypatch, tmp_path, runs=runs,
            measured_digests=[("sha256:tree-a", False), ("sha256:tree-b", False)],
        )
        assert [r["tree_moved"] for r in rec2["runs"]] == [False, True]
        ok2, reasons2 = ee.closes_issue(rec2)
        assert ok2 is False
        assert "pin-not-airtight" in reasons2

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
