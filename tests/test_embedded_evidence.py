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

from pathlib import Path

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
        # human. It was consumed by nothing (verified), so it is DELETED;
        # `module_forks_unexited` (the ordered counter) is the canonical hang
        # signal. This guards against reintroducing a misleading evidence field.
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


# ── same_file_list compares the red run's OWN observed files ──────────────

class TestSameFileListIsDerived:
    """The comparison's two sides must be INDEPENDENT data.

    `_run_once` records `files` as a copy of the selection, so a comparison against
    that field is a value compared with itself — `True` in every reachable state.
    The independent side is each run's junit-observed file set.
    """

    def test_a_red_that_observed_the_selection_is_certified(self):
        runs = [{
            "observed_files": ["tests/a.py", "tests/b.py"],
            "failing_files": ["tests/a.py"],
        }]
        assert ee._red_file_list_matches(runs, ["tests/a.py", "tests/b.py"]) is True

    def test_a_red_that_observed_a_different_file_list_is_not_certified(self):
        # The mutation that re-derives the comparison from the `files` copy makes
        # this assertion fail: `files` would match the selection while the run's
        # OWN junit shows it only ever ran one of the two files.
        runs = [{
            "files": ["tests/a.py", "tests/b.py"],
            "observed_files": ["tests/a.py"],
            "failing_files": ["tests/a.py"],
        }]
        assert ee._red_file_list_matches(runs, ["tests/a.py", "tests/b.py"]) is False

    def test_no_red_run_is_not_certified(self):
        assert ee._red_file_list_matches([], ["tests/a.py"]) is False

    def test_a_run_without_observed_files_is_not_certified(self):
        assert ee._red_file_list_matches([{}], ["tests/a.py"]) is False

    def test_a_red_that_failed_in_no_file_is_not_certified(self):
        runs = [{"observed_files": ["tests/a.py"], "failing_files": []}]
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
        assert ee._junit_test_files(p) == (
            ["tests/test_a.py", "tests/test_b.py"],
            ["tests/test_b.py"],
        )

    def test_a_missing_junit_yields_nothing(self, tmp_path: Path):
        assert ee._junit_test_files(tmp_path / "absent.xml") == ([], [])

    def test_a_junit_without_file_attributes_yields_nothing(self, tmp_path: Path):
        # This is exactly what pytest's default xunit2 emits — and it is why the
        # run command must pass `-o junit_family=xunit1`.
        p = tmp_path / "xunit2.xml"
        p.write_text(
            '<?xml version="1.0"?><testsuites><testsuite>'
            '<testcase classname="tests.test_a" name="test_ok" time="0.1" />'
            '</testsuite></testsuites>'
        )
        assert ee._junit_test_files(p) == ([], [])

    def test_the_run_command_requests_xunit1(self):
        cmd = ee._pytest_cmd(
            ["tests/a.py"], Path("/tmp/junit.xml"), "not track_b", 60
        )
        assert "junit_family=xunit1" in cmd


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
        assert ee._junit_test_files(p) == ([], [])
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
    exact vacuity `same_file_list` exists to prevent. This drives `_run_once` itself
    with a `_junit_test_files` that returns DISTINCT values, so the re-coupling
    mutation fails here.
    """

    def test_observed_files_come_from_the_junit_not_the_selection(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setattr(ee, "_child_env", lambda rr: {})
        monkeypatch.setattr(ee, "_snapshot_redis_logs", lambda rr: [])
        monkeypatch.setattr(ee, "load1", lambda: 1.0)
        monkeypatch.setattr(
            ee, "_read_junit_counts",
            lambda p: {"executed": 1, "skipped": 0, "failed": 1, "observed": 1,
                       "junit_parse_error": None},
        )
        monkeypatch.setattr(
            ee, "_junit_test_files",
            lambda p: (["tests/observed_only.py"], ["tests/observed_fail.py"]),
        )

        class GreenProc:
            returncode = 0

            def communicate(self, timeout=None):
                return ("", None)

            def kill(self):
                pass

        monkeypatch.setattr(ee.subprocess, "Popen", lambda *a, **k: GreenProc())
        rec = ee._run_once(["tests/selection_x.py"], tmp_path, tmp_path, 1, "m", 60)
        assert rec["observed_files"] == ["tests/observed_only.py"]
        assert rec["failing_files"] == ["tests/observed_fail.py"]
        # The selection copy is still recorded separately — the independence check
        # must read the junit side, which is NOT this field.
        assert rec["files"] == ["tests/selection_x.py"]
        assert rec["observed_files"] != rec["files"]


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
            "observed_files": list(files) if observed is None else list(observed),
            "failing_files": (
                [] if not red else (list(failing) if failing is not None else [files[0]])
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
