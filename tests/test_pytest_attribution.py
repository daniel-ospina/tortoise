"""pytest-attribution.sh — a failing pytest leg must be SELF-DESCRIBING (#3470).

The defect these tests pin is NOT "the log has no summary line". It is that the
log's *reporting step* could not be trusted to carry one:

* every leg printed a BLIND ``tail -n N /tmp/pytest.log``, a line-count window
  that any post-summary burst of interpreter-exit noise fills (the redislite
  ``__del__`` flood), pushing the session summary and the ``-r fEs`` report
  above the cut; and
* when a leg ended WITHOUT a summary, that fact was invisible — the leg
  contributed **zero** failure ids to the #3467 bar (``ci_exemption.py ids``
  → ``ids=0``), which is indistinguishable from a clean leg, so
  ``comm -23 pr-fails.txt main-fails.txt`` over the remaining legs was
  un-falsifiable rather than proven.

So the tests come in two layers, both of which EXECUTE:

1. the script, run as a real subprocess against fixture logs — the summary is
   found from the FULL log even when the tail window is pure noise, and the
   incomplete case is named; and
2. the rail — the ``::error::`` marker is fed through the real
   ``scripts/ci_exemption.py ids`` so the claim that an unobservable leg becomes
   an ATTRIBUTABLE failure (instead of a silent gap) is measured, not asserted.
   This layer is HOST-ONLY: the rail is agent-infra's, reached through a symlink
   that dangles on a runner, so both of its cases skip by name where the rail is
   absent (see ``RAIL_AVAILABLE``) — they never fail on that environment.

No DB, no network, no Docker: every input is a file this module writes to
``tmp_path``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "pytest-attribution.sh"
EXEMPTION_PY = REPO / "scripts" / "ci_exemption.py"
WORKFLOW = REPO / ".github" / "workflows" / "python-ci.yml"
# The reporter serves both workflows that print a `/tmp/*pytest.log` tail;
# post-merge-validation has no nodeid grep, so its tail was its ONLY channel.
ATTRIBUTION_WORKFLOWS = [WORKFLOW, REPO / ".github" / "workflows" / "post-merge-validation.yml"]

# The RAIL this module measures against lives in agent-infra, not here: `scripts`
# is a tracked symlink to `$AGENT_INFRA_PATH/scripts`, so `EXEMPTION_PY` resolves
# on a developer host and DANGLES on a runner. python-ci.yml is self-contained BY
# DECISION (fix #555: "no reusable-workflow call and no `scripts`/`agent-infra`
# symlinks — both resolve to paths that are broken on the runner") and no CI job
# checks out agent-infra, so the rail layer can only be EXECUTED where the rail
# exists. Off that host the two rail cases SKIP BY NAME rather than fail on an
# environment this repo does not own — and the skip is announced, so a green leg
# reads "the marker contract is pinned, the rail's parse was not run", never the
# vacuous "the rail agreed" that the marker itself exists to prevent. The marker
# TEXT stays pinned repo-side on EVERY run, by
# `test_marker_text_is_exactly_the_issue_contract`.
RAIL_AVAILABLE = EXEMPTION_PY.exists()
RAIL_ABSENT_REASON = (
    "agent-infra rail not checked out: `scripts/ci_exemption.py` is reached through the "
    "tracked `scripts` symlink, which dangles off a developer host (python-ci.yml is "
    "self-contained by decision, fix #555), so the rail's parse of the marker can only "
    "be executed where agent-infra is present"
)

INCOMPLETE_MARKER = "::error::test leg INCOMPLETE — no pytest summary; failure set unobservable"

# The redislite interpreter-exit flood: 4 lines per leaked server, written
# AFTER pytest's own summary. 90 servers = 360 lines, i.e. more than the widest
# `tail -n` window in the workflow (300); the real runs measured 18/88/178.
NOISE = "".join(
    "Exception ignored in: <function RedisMixin.__del__ at 0x100e1a3e0>\n"
    "Traceback (most recent call last):\n"
    '  File "/x/redislite/client.py", line 130, in __del__\n'
    "AttributeError: 'Redis' object has no attribute 'connection_pool'\n"
    for _ in range(90)
)

# Real session-summary shapes, copied from the observed job logs (run
# 36218536625: `1 error`; 36217447639: `1 failed`; 36216624712: `7 failed`;
# 36210912811: subtests; 36181757003: the embedded lane, 0:26:38 wall).
REAL_SUMMARIES = [
    "= 1165 passed, 22 skipped, 2 xfailed, 1 warning, 1 error in 163.66s (0:02:43) ==",
    "= 1 failed, 4467 passed, 118 skipped, 6 deselected, 21 warnings in 1112.94s (0:18:32) =",
    "====== 7 failed, 967 passed, 6 skipped, 1 warning in 279.96s (0:04:39) ========",
    "= 1 failed, 4840 passed, 58 skipped, 4 deselected, 15 warnings, 208 subtests passed in 1598.26s (0:26:38) =",
    "= 2 passed in 0.12s =",
    "= no tests ran in 0.03s =",
    "= 1 error in 0.44s =",
]

# Every OTHER `==== … ====` line a pytest log carries. None of these is the
# session summary, and a reporter that mistook one for it would silence the
# INCOMPLETE marker on exactly the runs that need it.
NON_SUMMARY_BANNERS = [
    "==================== pytest summary (tail) ====================",
    "==================== pytest failure nodeids ====================",
    "==================== pytest exit code: 137 ====================",
    "==================== WATCHDOG: pytest killed after 55m (1234 passed, 5 failed, 0 errored so far) — last test lines above ====================",
    "=========================== short test summary info ============================",
    "============================= slowest 15 durations =============================",
    "============================= test session starts =============================",
    "=================================== FAILURES ===================================",
    "116.35s call     tests/eval/write_path/test_write_path_benchmark.py::test_bpre_lane_determinism_and_provenance_regression_fails",
    "FAILED tests/test_oauth_token_fault.py::test_refresh_pre_mint_read_failure_is_503_not_500[organizations-None] - assert (200 == 503)",
]


def run_script(
    log: Path | None = None, *extra: str, rc: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the reporter the way the workflow does."""
    argv = ["bash", str(SCRIPT)]
    if log is not None:
        argv += ["--log", str(log)]
    if rc is not None:
        argv += ["--rc", rc]
    argv += list(extra)
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def summary_lines(stdout: str) -> list[str]:
    """The lines the reporter emitted under its own summary header."""
    head = "==================== pytest session summary (full log) ===================="
    assert head in stdout, f"the reporter did not print its own header:\n{stdout}"
    return [ln for ln in stdout.split(head, 1)[1].splitlines() if ln.strip()]


# ── 1. the script, executed ────────────────────────────────────────────────


@pytest.mark.parametrize("summary", REAL_SUMMARIES)
def test_every_real_session_summary_shape_is_reported(tmp_path: Path, summary: str) -> None:
    """Each observed pytest summary shape is recognised, verbatim."""
    log = tmp_path / "pytest.log"
    log.write_text(f"tests/test_a.py::test_one PASSED [ 50%]\n\n{summary}\n")
    result = run_script(log, rc="1")
    assert result.returncode == 0, result.stderr
    assert summary in result.stdout
    assert INCOMPLETE_MARKER not in result.stdout


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("\x1b[32m= 1 passed in 0.10s =\x1b[0m", "= 1 passed in 0.10s ="),
        ("=\x1b[1m\x1b[31m 1 failed\x1b[0m in 2.00s =", "= 1 failed in 2.00s ="),
        (
            "\x1b[36;1m====== 7 failed, 967 passed in 279.96s (0:04:39) ========\x1b[0m",
            "====== 7 failed, 967 passed in 279.96s (0:04:39) ========",
        ),
    ],
)
def test_colourised_summary_is_still_found(tmp_path: Path, raw: str, expected: str) -> None:
    """SGR markup must not turn a healthy leg into a false INCOMPLETE.

    A false INCOMPLETE creates a spurious failure key — the INVERSE of the
    defect this reporter exists for — so the read strips SGR exactly as
    ``scripts/ci_exemption.py`` strips it from a ``--log-failed`` capture.
    """
    log = tmp_path / "pytest.log"
    log.write_text(raw + "\n")
    result = run_script(log, rc="0")
    assert result.returncode == 0, result.stderr
    assert INCOMPLETE_MARKER not in result.stdout
    assert expected in result.stdout


@pytest.mark.parametrize("banner", NON_SUMMARY_BANNERS)
def test_no_other_banner_is_mistaken_for_the_summary(tmp_path: Path, banner: str) -> None:
    """A non-summary banner alone must NOT silence the INCOMPLETE marker.

    A reporter that accepted one of these as "the summary" would print a
    plausible-looking attribution line and drop the marker — the leg would read
    as attributable when it is not, which is the failure mode this whole change
    exists to end.
    """
    log = tmp_path / "pytest.log"
    log.write_text(f"{banner}\n")
    result = run_script(log, rc="137")
    assert result.returncode == 0, result.stderr
    assert INCOMPLETE_MARKER in result.stdout
    # …and the banner itself is not echoed as if it were a result.
    assert banner not in summary_lines(result.stdout)


def test_summary_above_a_noise_starved_tail_still_reaches_the_report(tmp_path: Path) -> None:
    """THE #3470 reproduction: the tail window is pure noise, the summary is not.

    This is the case the old `tail -n 300` could not survive. The reporter reads
    the FULL log, so the summary is reported regardless of what the last N lines
    hold.
    """
    summary = "= 4 failed, 2360 passed, 18 skipped, 20 warnings in 358.47s (0:05:58) =====\n"
    log = tmp_path / "pytest.log"
    log.write_text("tests/test_x.py::test_a PASSED [ 50%]\n" + summary + NOISE)
    noise_lines = [ln for ln in log.read_text().splitlines() if ln.strip()]
    assert len(noise_lines) > 300, "fixture must out-noise the workflow's tail window"

    result = run_script(log, rc="1")
    assert result.returncode == 0, result.stderr
    assert summary.strip() in result.stdout
    assert INCOMPLETE_MARKER not in result.stdout

    # The premise, stated so the test cannot rot into a tautology: the blind
    # tail the workflow used to print would have MISSED this summary.
    tail_window = "\n".join(noise_lines[-300:])
    assert summary.strip() not in tail_window


def test_incomplete_leg_is_named_and_does_not_change_the_status(tmp_path: Path) -> None:
    """A log with no summary at all is reported as INCOMPLETE, at exit 0."""
    log = tmp_path / "pytest.log"
    log.write_text("tests/test_a.py::test_one PASSED [  1%]\ntests/test_a.py::test_two ")
    result = run_script(log, rc="137")
    assert result.returncode == 0, result.stderr
    assert INCOMPLETE_MARKER in result.stdout
    # The rc is carried for the human reader, and the reader is warned off the
    # "no FAILED lines == pass" misreading.
    assert "rc=137" in result.stdout
    assert "do NOT read the absence of FAILED lines" in result.stdout


def test_missing_log_is_named(tmp_path: Path) -> None:
    """A leg that produced no log file is INCOMPLETE, not silently clean."""
    result = run_script(tmp_path / "does-not-exist.log", rc="1")
    assert result.returncode == 0, result.stderr
    assert INCOMPLETE_MARKER in result.stdout


def test_marker_text_is_exactly_the_issue_contract(tmp_path: Path) -> None:
    """The marker is pinned VERBATIM — it is the contract a reader greps for."""
    log = tmp_path / "pytest.log"
    log.write_text("no summary here\n")
    result = run_script(log, rc="2")
    assert INCOMPLETE_MARKER in result.stdout


# ── 1b. the in-flight test: a KILL names what it killed ────────────────────

IN_FLIGHT_HEAD = "==================== in-flight test(s):"


def in_flight_lines(stdout: str) -> list[str]:
    """The nodeids the reporter named as still running when the leg died."""
    tail = stdout.split(IN_FLIGHT_HEAD, 1)[1]
    return [ln.strip() for ln in tail.splitlines() if ln.startswith("  ")]


# Verbatim shapes from the two kills MEASURED on main 2026-10-19 (the artifacts
# `pytest-log-test-slow-a` / `pytest-log-test-a`). Both legs ended without a
# session summary and neither named a test, so the only route to the culprit was
# a 300-line tail or a faulthandler dump read by hand.
KILLED_SERIAL_LOG = (
    "tests/test_import_endpoint.py::TestImportHappyPath::test_import_happy_path PASSED [ 60%]\n"
    "tests/test_import_endpoint.py::test_the_client_seam_turns_a_wait_bound_refusal_into_an_inconclusive_skip PASSED [ 60%]\n"
    "tests/test_ep_selector.py::test_parity_anchors_equals_context "
)

KILLED_BY_PER_TEST_TIMEOUT_LOG = """tests/test_session_key_http.py::TestSessionKeyMintConcurrency::test_registry_mint_blocks_while_team_lock_held FAILED [ 86%]
tests/test_session_key_http.py::TestSessionKeyMintConcurrency::test_concurrent_recovery_mints_stay_at_cap Timeout (0:04:40)!
Thread 0x00007f7aa8cf76c0 (most recent call first):
  File "/home/runner/work/tortoise/tortoise/tests/test_session_key_http.py", line 802 in test_concurrent_recovery_mints_stay_at_cap
  File "/opt/hostedtoolcache/Python/3.12.15/x64/lib/python3.12/threading.py", line 355 in wait
"""


@pytest.mark.parametrize(
    "log_text,expected",
    [
        # serial kill: the nodeid line never received a result verb
        (KILLED_SERIAL_LOG, "tests/test_ep_selector.py::test_parity_anchors_equals_context"),
        # killed AFTER pytest-timeout fired and then wedged the leg
        # killed AFTER pytest-timeout fired: the raw line is kept, so the suffix
        # that says WHY it was stuck travels with the name
        (
            KILLED_BY_PER_TEST_TIMEOUT_LOG,
            "tests/test_session_key_http.py::TestSessionKeyMintConcurrency::test_concurrent_recovery_mints_stay_at_cap Timeout (0:04:40)!",
        ),
    ],
)
def test_killed_leg_names_the_test_it_was_running(
    tmp_path: Path, log_text: str, expected: str
) -> None:
    """A kill names the in-flight test — the difference between a flake and a bug.

    Both shapes are real: the serial one is `test-slow (a)` killed at its 40m
    watchdog on the self-hosted ARM64 pool (42.1m of job wall, `ci_crit_max`),
    and the timeout one is `test (a)` killed at 20m after its progress counter
    sat frozen for three 5-minute heartbeats.
    """
    log = tmp_path / "pytest.log"
    log.write_text(log_text)
    result = run_script(log, rc="137")
    assert result.returncode == 0, result.stderr
    assert INCOMPLETE_MARKER in result.stdout
    assert in_flight_lines(result.stdout) == [expected], result.stdout


def test_result_lines_are_not_mistaken_for_in_flight(tmp_path: Path) -> None:
    """`-r fEs` and `--durations` quote nodeids too, and neither is in flight.

    A reporter that matched a nodeid ANYWHERE on a line would name a test that
    had already reported — inverting the one fact this section exists to state.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/test_a.py::test_one PASSED [ 50%]\n"
        "FAILED tests/test_a.py::test_two - assert (200 == 503)\n"
        "116.35s call     tests/test_a.py::test_three\n"
        "tests/test_a.py::test_four "
    )
    result = run_script(log, rc="137")
    assert in_flight_lines(result.stdout) == ["tests/test_a.py::test_four"], result.stdout


# Verbatim shapes from pytest 9.1.1 + pytest-xdist 3.8.0 (executed, not recalled):
# with `-n 2 --dist loadscope -v` the RESULT is on a later line and carries the
# verdict FIRST (`[gw0] [ 33%] PASSED tests/x.py::t`), while the start line is a
# bare nodeid — and a verbose skip appends an abbreviated reason to the start line
# (`tests/x.py::t SKIPPED (...) [ 80%]`). Both were measured by killing a real
# xdist session.
XDIST_KILLED_LOG = (
    "tests/test_k.py::test_param[first ok] \n"
    "[gw0] [ 33%] PASSED tests/test_k.py::test_param[first ok] \n"
    "tests/test_k.py::test_skipme SKIPPED (...) [ 66%]\n"
    "tests/test_k2.py::test_boom\n"
    "[gw1] [ 66%] FAILED tests/test_k2.py::test_boom \n"
    "tests/test_k.py::test_wedge \n"
)

# The P1 shape: a parametrize id containing a SPACE, whose truncated prefix is also
# the key of an already-PASSED sibling. An id-character allow-list truncates both to
# `tests/test_q.py::test_param[map-shape`, marks that key done, and then reports
# "none unresolved" while the real culprit is right there in the log.
PARAMETRIZE_ID_WITH_SPACE_LOG = (
    "tests/test_q.py::test_param[map-shape (control)] PASSED [ 50%]\n"
    "tests/test_q.py::test_param[map-shape (variant)] \n"
)


def test_xdist_result_lines_do_not_accuse_a_test_that_already_passed(tmp_path: Path) -> None:
    """Under xdist the verdict is on a LATER line; the innocent must stay innocent.

    Measured: `-n 2 --dist loadscope -v` prints `<nodeid>`, then
    `[gw0] [ 33%] PASSED <nodeid>`. A parser that only closes a nodeid on its own
    line reports every completed test as "in flight" — up to four false
    accusations on the very workflow that keeps `-n 4` one line away.
    """
    log = tmp_path / "pytest.log"
    log.write_text(XDIST_KILLED_LOG)
    result = run_script(log, rc="124")
    assert in_flight_lines(result.stdout) == ["tests/test_k.py::test_wedge"], result.stdout


def test_parametrized_id_with_a_space_is_named_in_full_and_never_swallowed(
    tmp_path: Path,
) -> None:
    """A nodeid is not a whitespace-delimited token (measured id: `map-shape (control)`).

    This is the falsehood case, not the cosmetics case: an allow-list of id
    characters truncates both ids to `tests/test_q.py::test_param[map-shape`, the
    PASSED sibling marks that key done, and the report then states "none
    unresolved" — the opposite of the log. The full id must be reported, so it can
    be copied and re-run.
    """
    log = tmp_path / "pytest.log"
    log.write_text(PARAMETRIZE_ID_WITH_SPACE_LOG)
    result = run_script(log, rc="137")
    assert in_flight_lines(result.stdout) == [
        "tests/test_q.py::test_param[map-shape (variant)]"
    ], result.stdout


@pytest.mark.parametrize("rc", ["124", "2"])
def test_a_sigint_kill_that_still_wrote_a_summary_names_its_test(
    tmp_path: Path, rc: str
) -> None:
    """rc=124/2 write a summary that OMITS the interrupted test — name it anyway.

    pytest catches the watchdog's SIGINT and reports the tests it completed, so
    "a summary exists" is not "the leg was not killed": the leg then looks like a
    clean run of everything before the interrupt. The in-flight section must not
    be gated on the summary's absence.
    """
    summary = "= 1 passed in 5.03s ="
    log = tmp_path / "pytest.log"
    log.write_text(f"tests/test_a.py::test_one PASSED [ 50%]\ntests/test_a.py::test_two \n{summary}\n")
    result = run_script(log, rc=rc)
    assert summary in result.stdout
    assert in_flight_lines(result.stdout) == ["tests/test_a.py::test_two"], result.stdout
    # ⛔ The OTHER half of this case, and the one a leg's #3467 key depends on: pytest
    # unwound far enough to write a summary, but the suite was CUT SHORT — every test
    # after the interrupt never ran, so the set is unobservable and the marker must
    # fire even though a summary exists.
    assert INCOMPLETE_MARKER in result.stdout, result.stdout


# Verbatim extract of the real 2026-10-19 `test-slow (a)` artifact
# (`pytest-log-test-slow-a`, job 118285607896): a COMPLETED nested pytest session
# (a test that shells out to pytest) writes its own banner + summary, then the
# leg's own session starts and is killed. The nested summary is the ONLY SUMMARY_RE
# match in the file.
NESTED_SESSION_THEN_KILLED_LOG = """============================= test session starts ==============================
collected 3 items

tests/test_event_store.py::test_seq_is_monotonic_under_concurrency_live_falkor PASSED [ 33%]
tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes PASSED [100%]

============================== 3 passed in 51.08s ==============================
tests/test_reaper.py::test_discover_skips_corrupt_settings_continues_sweep PASSED [ 56%]
tests/test_import_endpoint.py::test_import_happy_path PASSED [ 60%]
tests/test_ep_selector.py::test_parity_anchors_equals_context \
"""


def test_a_nested_session_summary_is_named_stale_and_never_suppresses_incomplete(
    tmp_path: Path,
) -> None:
    """The measured FALSE CLEAN: a nested run's summary is not this leg's verdict.

    Every `test-slow (a)` log carries a completed nested pytest session before the
    leg's own. Reporting that summary as the leg's verdict silences the INCOMPLETE
    marker, so a leg killed at its 40m budget with an unobservable failure set
    reads as "a clean 51 s run" — the mistriage this file exists to prevent. The
    stale summary must be NAMED (a reader greps this log and will find it) and the
    marker must still fire.
    """
    log = tmp_path / "pytest.log"
    log.write_text(NESTED_SESSION_THEN_KILLED_LOG)
    result = run_script(log, rc="137")
    assert "STALE session summary" in result.stdout, result.stdout
    assert "3 passed in 51.08s" in result.stdout
    assert INCOMPLETE_MARKER in result.stdout, result.stdout
    assert in_flight_lines(result.stdout) == [
        "tests/test_ep_selector.py::test_parity_anchors_equals_context"
    ], result.stdout


# The shape that defeats a POSITIONAL ownership rule: the nested run is the LAST
# thing in the log (the kill landed after its child finished), so there is no
# progress line after its summary — and a rule that only asks "does a start line
# follow the summary?" answers YES-this-is-the-leg's and reports a clean 3-test
# verdict for a leg that never finished.
NESTED_SESSION_ENDS_LOG = """============================= test session starts ==============================
collected 40 items

tests/test_a.py::test_one
tests/test_nested.py::test_runs_pytest
============================= test session starts ==============================
collected 3 items

tests/nested_a.py::test_x PASSED [ 33%]
tests/nested_b.py::test_y PASSED [ 66%]
tests/nested_c.py::test_z PASSED [100%]
============================== 3 passed in 51.08s ==============================
"""


def test_nested_session_that_ends_the_log_is_still_not_the_legs_verdict(
    tmp_path: Path,
) -> None:
    """Ownership is decided by the SESSION STACK, not by position.

    A session's summary is the last thing it prints only when that session
    COMPLETED — which a kill path cannot assume. Here the nested session closed
    and the outer one never did, so the stack is open at EOF and the summary
    belongs to the child. Called the leg's verdict, it reports "3 passed" for a
    leg killed at its budget, and suppresses the marker that gives the leg its
    #3467 key.
    """
    log = tmp_path / "pytest.log"
    log.write_text(NESTED_SESSION_ENDS_LOG)
    result = run_script(log, rc="137")
    assert "STALE session summary" in result.stdout, result.stdout
    assert INCOMPLETE_MARKER in result.stdout, result.stdout
    # both outer-session tests are unresolved: neither printed a result
    assert in_flight_lines(result.stdout) == [
        "tests/test_a.py::test_one",
        "tests/test_nested.py::test_runs_pytest",
    ], result.stdout


def test_the_real_truncated_artifact_shape_is_not_read_as_a_clean_run(tmp_path: Path) -> None:
    """The measured shape that defeated a POSITIONAL ownership rule.

    `head -27` of the real 2026-10-19 `test-slow (a)` artifact is a COMPLETE nested
    session (its own banner, 3 tests, its summary) followed by a 33 KB NUL hole that
    swallowed the leg's own header. There is no start line after the summary and only
    one banner, so "does a start line follow it?" answers "no" — and the leg was
    SIGKILLed at its 40m budget. Only the NUL signature distinguishes it.
    """
    body = (
        "============================= test session starts ==============================\n"
        "collected 3 items\n\n"
        "tests/test_event_store.py::test_seq_is_monotonic_under_concurrency_live_falkor PASSED [ 33%]\n"
        "tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes PASSED [100%]\n\n"
        "============================== 3 passed in 51.08s ==============================\n"
    ).encode() + b"\x00" * 4096
    log = tmp_path / "pytest.log"
    log.write_bytes(body)
    result = run_script(log, rc="137")
    assert "STALE session summary" in result.stdout, result.stdout
    assert INCOMPLETE_MARKER in result.stdout, result.stdout


def test_a_crlf_log_is_read_like_an_lf_one(tmp_path: Path) -> None:
    """One normalisation for every pipeline in this file (`tr -d '\r\000'`).

    With `\r` left in, the `$` anchors in SUMMARY_RE and in the banner match are
    defeated, and a completed leg is reported INCOMPLETE — the inverse marker. This
    also matters for logs re-read from a `gh run view` capture.
    """
    log = tmp_path / "pytest.log"
    log.write_bytes(
        b"============================= test session starts ==============================\r\n"
        b"tests/test_a.py::test_one PASSED [ 50%]\r\n"
        b"= 2 passed in 0.12s =\r\n"
    )
    result = run_script(log, rc="0")
    assert "2 passed in 0.12s" in result.stdout, result.stdout
    assert INCOMPLETE_MARKER not in result.stdout, result.stdout


def test_the_legs_own_summary_after_its_last_test_is_still_trusted(tmp_path: Path) -> None:
    """The other side of the same rule: a real verdict is NOT called stale.

    A summary is the last thing a session prints, so `summary` after the final
    test start line is this leg's own — the strictness above must not turn every
    green leg into a false INCOMPLETE (the inverse marker `test_a_log_with_nul…`
    already guards for binary logs).
    """
    summary = "= 1 passed in 4.20s ="
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/test_a.py::test_one\n"
        "\n"
        f"{summary}\n"
        "Exception ignored in: <function RedisMixin.__del__ at 0x10>\n"
    )
    result = run_script(log, rc="0")
    assert summary in result.stdout
    assert "STALE session summary" not in result.stdout
    assert INCOMPLETE_MARKER not in result.stdout


def test_an_interrupted_leg_with_failures_and_no_summary_still_names_its_test(
    tmp_path: Path,
) -> None:
    """rc=2 is not only the collection-error code (measured) — it is INTERRUPTED too.

    Keyed on rc alone, this leg would be mistaken for a collection error and report
    nothing at all: no marker, no culprit. The distinction is the log's own
    `Interrupted: N error` collection signature, so an interrupted run that failed
    early and then lost its log gets both.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/test_a.py::test_one PASSED [ 10%]\n"
        "FAILED tests/test_a.py::test_two - assert 1 == 2\n"
        "tests/test_a.py::test_three \n"
    )
    result = run_script(log, rc="2")
    assert INCOMPLETE_MARKER in result.stdout, result.stdout
    assert in_flight_lines(result.stdout) == ["tests/test_a.py::test_three"], result.stdout


# Measured with pytest-subtests (`-v`): a subtest prints its OWN progress line and the
# parent's `PASSED` line comes last, with no bare parent start line in between.
SUBTEST_COMPLETED_LOG = (
    "collected 4 items\n"
    "tests/test_sub.py::test_ok PASSED [ 25%]\n"
    "tests/test_sub.py::test_pass_with_subtests SUBPASSED[case] (i=0)         [ 50%]\n"
    "tests/test_sub.py::test_pass_with_subtests SUBPASSED[case] (i=1)         [ 50%]\n"
    "tests/test_sub.py::test_pass_with_subtests PASSED [ 75%]\n"
    "tests/test_sub.py::test_wedge \n"
)


def test_completed_subtests_are_not_accused(tmp_path: Path) -> None:
    """A `SUB*` verdict line is a RESULT line, not a start line (measured shape).

    `tests/x.py::t SUBPASSED[case] (i=0)  [ 50%]` matches none of the plain result
    verbs, so a parser that only allow-lists those reads every completed subtest as
    in flight — reporting a test that PASSED as the culprit. This repo's own logs
    record a leg with `208 subtests passed`, so the shape is live.
    """
    log = tmp_path / "pytest.log"
    log.write_text(SUBTEST_COMPLETED_LOG)
    result = run_script(log, rc="137")
    assert in_flight_lines(result.stdout) == ["tests/test_sub.py::test_wedge"], result.stdout


def test_an_xdist_prefixed_subtest_line_does_not_fabricate_a_parent(tmp_path: Path) -> None:
    """With xdist the SUB line puts the verdict FIRST and the nodeid LAST (measured).

    `[gw0] [100%] SUBPASSED[case] (i=0) tests/test_sub.py::t` — taking "everything
    before SUB" as the parent yields `[gw0] [100%]`, a test that can never be closed,
    which both invents a culprit and can crowd the real one out of the 4-name budget.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/test_sub.py::test_a \n"
        "[gw0] [100%] SUBPASSED[case] (i='0') tests/test_sub.py::test_a \n"
        "[gw0] [100%] SUBPASSED[case] (i='1') tests/test_sub.py::test_a \n"
        "[gw0] [100%] PASSED tests/test_sub.py::test_a \n"
        "tests/test_sub.py::test_wedge \n"
    )
    result = run_script(log, rc="137")
    named = in_flight_lines(result.stdout)
    assert named == ["tests/test_sub.py::test_wedge"], result.stdout
    assert not any("[gw" in n for n in named), named


def test_a_kill_inside_a_subtest_names_the_parent(tmp_path: Path) -> None:
    """The subtest line registers its PARENT, so a kill inside one is attributable."""
    log = tmp_path / "pytest.log"
    log.write_text(
        "collected 1 item\n"
        "tests/test_sub.py::test_pass_with_subtests SUBPASSED[case] (i=0)         [100%]\n"
    )
    result = run_script(log, rc="137")
    assert in_flight_lines(result.stdout) == [
        "tests/test_sub.py::test_pass_with_subtests"
    ], result.stdout


def test_a_nested_collection_error_cannot_silence_a_killed_legs_marker(
    tmp_path: Path,
) -> None:
    """The collection-error suppression must never apply to a KILL.

    A nested pytest run's raw stdout lands in the leg's log (the measured artifact
    carries a whole nested session), so a child that hits a collection error would
    otherwise switch off the marker on a leg that was then killed — restoring exactly
    the "leg contributes nothing to the #3467 set" defect this file exists for.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "ERROR tests/nested_bad.py\n"
        "!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
        "=============================== 1 error in 0.09s ===============================\n"
        "tests/test_a.py::test_two \n"
    )
    result = run_script(log, rc="137")
    assert INCOMPLETE_MARKER in result.stdout, result.stdout
    assert in_flight_lines(result.stdout) == ["tests/test_a.py::test_two"], result.stdout
    # and the KILL explanation, not the collection-error one (a kill is never a
    # collection error, whatever else the log carries)
    assert "COLLECTION ERROR" not in result.stdout, result.stdout


def test_the_legs_own_summary_wins_over_a_nested_one(tmp_path: Path) -> None:
    """WHICH summary is served: the leg's own (the LAST), never the nested child's.

    A log can carry both — the nested session's finishes early and the leg's closes the
    file. Serving the first would report `3 passed in 51.08s` for a leg whose own
    verdict is `2 failed, 38 passed`, the exact inversion the header claims to fix.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "============================= test session starts ==============================\n"
        "collected 40 items\n\n"
        "tests/test_nested.py::test_runs_pytest \n"
        "============================= test session starts ==============================\n"
        "collected 3 items\n\n"
        "tests/nested_a.py::test_x PASSED [ 33%]\n"
        "============================== 3 passed in 51.08s ==============================\n"
        "tests/test_a.py::test_one PASSED [ 50%]\n"
        "========================= 2 failed, 38 passed in 300.00s ========================\n"
    )
    result = run_script(log, rc="1")
    assert "2 failed, 38 passed in 300.00s" in result.stdout, result.stdout
    assert "3 passed in 51.08s" not in result.stdout, result.stdout


def test_a_completed_leg_mentioning_keyboardinterrupt_is_not_a_kill(tmp_path: Path) -> None:
    """The no-`--rc` kill heuristic must not fire on a test that RAISES it.

    This repo's own tests raise `KeyboardInterrupt` deliberately
    (tests/test_ship_test_onboarding.py), so a completed leg's FAILURES section can
    contain the word. Reading that as a kill reports a finished leg as unfinished —
    the inverse marker.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/test_a.py::test_one PASSED [ 50%]\n"
        "KeyboardInterrupt: driver stop was killed\n"
        "= 1 passed in 4.20s =\n"
    )
    result = run_script(log)  # no --rc
    assert "4.20s" in result.stdout
    assert INCOMPLETE_MARKER not in result.stdout, result.stdout


def test_an_interrupt_banner_without_rc_is_still_a_kill(tmp_path: Path) -> None:
    """...but pytest's OWN interrupt banner is, even with no `--rc`."""
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/test_s.py::test_sleeps \n"
        "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! KeyboardInterrupt !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"
        "tests/test_s.py:3: KeyboardInterrupt\n"
    )
    result = run_script(log)  # no --rc
    assert INCOMPLETE_MARKER in result.stdout, result.stdout
    assert in_flight_lines(result.stdout) == ["tests/test_s.py::test_sleeps"], result.stdout


def test_a_missing_rc_still_recognises_a_kill_from_the_log(tmp_path: Path) -> None:
    """`--rc` is optional, so the kill must also be readable from the log itself.

    Run by hand against a downloaded log — the natural way a triager uses this file —
    a killed leg whose log carries pytest-timeout's own `Timeout (0:04:40)!` must not
    be served as a clean partial run. Omitting `--rc` was exactly that false clean.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/test_a.py::test_one PASSED [ 50%]\n"
        "tests/test_a.py::test_sleeps Timeout (0:04:40)!\n"
        "= 1 passed in 6.08s =\n"
    )
    result = run_script(log)  # no --rc AT ALL
    assert INCOMPLETE_MARKER in result.stdout, result.stdout
    assert in_flight_lines(result.stdout) == [
        "tests/test_a.py::test_sleeps Timeout (0:04:40)!"
    ], result.stdout


def test_rc2_without_the_collection_signature_does_not_claim_a_collection_error(
    tmp_path: Path,
) -> None:
    """The collection-error explanation is keyed on the LOG, never on rc alone.

    rc=2 is also pytest's INTERRUPTED code; printing "1 error during collection … the
    attributable record is the ERROR <path> line" for a leg that has neither is a
    false statement about a log the reader is looking at.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/test_a.py::test_one PASSED [ 10%]\n"
        "tests/test_a.py::test_two PASSED [ 20%]\n"
    )
    result = run_script(log, rc="2")
    assert INCOMPLETE_MARKER in result.stdout, result.stdout
    # The assert names the branch this fixture actually REACHES, and it does so in
    # that branch's own words. A negative assertion on the COLLECTION ERROR text
    # would discriminate nothing here: with rc=2 and no collection signature
    # `KILLED=1`, while that paragraph is gated on `KILLED = 0`, so the text could
    # never appear whatever the script did with rc. The positive side of the
    # collection-error text is pinned by
    # `test_a_quiet_collection_error_is_explained_not_a_kill`, from a fixture that
    # DOES reach it.
    assert "the kill landed between tests" in result.stdout, result.stdout


def test_a_quiet_collection_error_is_explained_not_a_kill(tmp_path: Path) -> None:
    """The COLLECTION ERROR paragraph is reachable — and says what it claims.

    This is the same defect the reporter was written for, seen from the other side:
    a log WINDOW (the old blind `tail -300`) that kept pytest's
    `Interrupted: N error during collection` line but cut away the `ERROR <path>`
    record above it, and whose summary is a `-q` one (`1 error in 0.09s`, no `====`
    banner, so SUMMARY_RE cannot match it). Nothing here is a kill: rc=1, no
    start line, no NUL hole — so the reader must be told the attributable record is
    the `ERROR <path>` line rather than be sent hunting for a culprit test.

    The text alone is not the point: the paragraph is the LAST arm of the
    `none unresolved` elif chain, so a reordering that let the generic
    `rc=$RC without a session summary` arm win would still leave the log
    "explained", just wrongly. That reordering reds this test.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
        "1 error in 0.09s\n"
    )
    result = run_script(log, rc="1")
    assert result.returncode == 0, result.stderr
    # No attributable record survived the window, so the marker still stands.
    assert INCOMPLETE_MARKER in result.stdout, result.stdout
    assert "none unresolved" in result.stdout, result.stdout
    assert "this log carries a COLLECTION ERROR" in result.stdout, result.stdout
    assert "attributable record is the 'ERROR <path>' line" in result.stdout, result.stdout
    # Not a kill, and not the generic "rc without a summary" paragraph either.
    assert "the kill landed between tests" not in result.stdout, result.stdout
    assert "without a session" not in result.stdout, result.stdout


def test_collection_error_is_attributed_not_labelled_unobservable(tmp_path: Path) -> None:
    """rc=2 is pytest's COLLECTION-ERROR code too (measured), not only its SIGINT.

    Two claims must not be made about such a leg: that it was killed mid-test (it
    never started one), and that its failure set is unobservable — `ERROR <path>` IS
    the record, and the rail reads it, so the INCOMPLETE marker would add a second,
    false key to a leg that is already attributable.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "ERROR tests/test_bad.py\n"
        "!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
        "=============================== 1 error in 0.09s ===============================\n"
    )
    result = run_script(log, rc="2")
    assert INCOMPLETE_MARKER not in result.stdout, result.stdout
    assert "what the leg was doing when it died" not in result.stdout, result.stdout
    # It reports its own summary and NOTHING else: no marker, no kill section.
    assert result.stdout.strip().splitlines() == [
        "==================== pytest session summary (full log) ====================",
        "=============================== 1 error in 0.09s ===============================",
    ], result.stdout


def test_a_killed_leg_keeps_the_marker_even_when_it_failed_earlier(tmp_path: Path) -> None:
    """A kill's set is INCOMPLETE even with FAILED lines: the tests that never ran.

    The suppression above is deliberately narrow (rc=2 + attributable records). A
    leg SIGKILLed at its budget after two failures is still unobservable — its
    remaining tests never executed — so the marker must fire.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/test_a.py::test_one PASSED [ 10%]\n"
        "FAILED tests/test_a.py::test_two - assert 1 == 2\n"
        "tests/test_a.py::test_three \n"
    )
    result = run_script(log, rc="137")
    assert INCOMPLETE_MARKER in result.stdout, result.stdout
    assert in_flight_lines(result.stdout) == ["tests/test_a.py::test_three"], result.stdout


def test_in_flight_section_says_none_when_every_start_has_a_result(tmp_path: Path) -> None:
    """A kill BETWEEN tests must not fabricate a suspect."""
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/test_a.py::test_one PASSED [ 50%]\n"
        "tests/test_a.py::test_two PASSED [100%]\n"
        "[kill arrives here, between tests]\n"
    )
    result = run_script(log, rc="137")
    assert in_flight_lines(result.stdout) == [], result.stdout
    assert "none unresolved" in result.stdout, result.stdout


def test_an_owned_summary_on_a_finished_leg_never_names_a_test(tmp_path: Path) -> None:
    """A leg that finished must never print "what the leg was doing when it died".

    Stray unclosed start lines reach a leg's log from a nested/foreign run's captured
    stdout. On a leg that OWNS its summary and was not killed, naming one would send a
    triager after a test that was never in flight — so the in-flight section must not
    appear at all.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "tests/test_a.py::test_one PASSED [ 50%]\n"
        "tests/test_stray.py::test_never_closed \n"
        "= 2 passed in 4.20s =\n"
    )
    result = run_script(log, rc="0")
    assert "4.20s" in result.stdout
    assert IN_FLIGHT_HEAD not in result.stdout, result.stdout
    assert INCOMPLETE_MARKER not in result.stdout, result.stdout


def test_healthy_leg_reports_its_summary_and_no_in_flight_section(tmp_path: Path) -> None:
    """The new section rides the INCOMPLETE path only — never a healthy leg."""
    summary = "= 2 passed in 4.20s ="
    log = tmp_path / "pytest.log"
    log.write_text(f"tests/test_a.py::test_one PASSED [ 50%]\n{summary}\n")
    result = run_script(log, rc="0")
    assert summary in result.stdout
    assert IN_FLIGHT_HEAD not in result.stdout


def test_a_stray_nul_byte_does_not_turn_a_green_leg_into_incomplete(tmp_path: Path) -> None:
    """Only a NUL RUN is the truncation signature — a stray byte is test output.

    A test (or a subprocess it captures) can write a single NUL to stdout. Treating
    "any NUL" as "truncated" would report a cleanly completed leg as STALE +
    INCOMPLETE — the false marker the file's header calls the inverse of the defect
    it exists for. The measured hole is 33 KB, so a run threshold separates them.
    """
    summary = "= 2 passed in 1.00s ="
    log = tmp_path / "pytest.log"
    log.write_bytes(
        b"tests/test_a.py::test_one PASSED [ 50%]\n" + summary.encode() + b"\n\x00"
    )
    result = run_script(log, rc="0")
    assert summary in result.stdout
    assert "STALE session summary" not in result.stdout, result.stdout
    assert INCOMPLETE_MARKER not in result.stdout, result.stdout


def test_a_nul_bearing_log_is_not_trusted_as_the_legs_verdict(tmp_path: Path) -> None:
    """NUL bytes are the truncation signature — ownership becomes UNPROVABLE.

    Measured on the 2026-10-19 `test-slow (a)` kill: the log carries a 33 KB NUL
    hole because a nested run's `>` redirect rewrote the file mid-session, which is
    also how the leg's own session header disappeared. So a summary found in such a
    log may be the CHILD's (the real artifact's is: "3 passed in 51.08s", while the
    leg was killed at 40m). The file's rule is "unprovable -> STALE", and the marker
    must still fire — the alternative is a red leg read as a clean 51 s run.
    """
    summary = "= 2 passed in 4.20s ="
    log = tmp_path / "pytest.log"
    log.write_bytes(
        b"tests/test_a.py::test_one PASSED [ 50%]\n\n" + b"\x00" * 4096 + b"\n"
        + summary.encode() + b"\n"
    )
    result = run_script(log, rc="137")
    assert summary in result.stdout
    assert "STALE session summary" in result.stdout, result.stdout
    assert INCOMPLETE_MARKER in result.stdout, result.stdout


@pytest.mark.parametrize(
    "argv",
    [
        [],  # no --log at all
        ["--log"],  # flag with no value
        ["--log", ""],  # empty value
        ["--rc"],  # flag with no value
        ["--wat"],  # unknown argument
    ],
)
def test_bad_invocation_is_exit_2(argv: list[str]) -> None:
    """A misuse is a usage error, so a typo cannot read as a clean report."""
    result = subprocess.run(
        ["bash", str(SCRIPT), *argv], capture_output=True, text=True, check=False
    )
    assert result.returncode == 2, f"argv={argv} -> rc={result.returncode}"
    assert "::error::pytest-attribution:" in result.stdout


# ── 2. the rail, executed — the marker is ATTRIBUTABLE ─────────────────────


def _capture(line: str) -> str:
    """A `gh run view --log-failed`-shaped capture (job \\t step \\t ts Z content)."""
    ts = "2026-09-26T04:46:51.5423005Z"
    return (
        f"test (b)\tRun fast test suite\t{ts} {line}\n"
        f"test (b)\tRun fast test suite\t{ts} ==================== pytest exit code: 137 ====================\n"
        f"test (b)\tRun fast test suite\t2026-09-26T04:46:56.5347076Z ##[error]Process completed with exit code 137.\n"
    )


def _rail_ids(capture_text: str, tmp_path: Path) -> tuple[str, int]:
    cap = tmp_path / "capture.txt"
    cap.write_text(capture_text)
    result = subprocess.run(
        [sys.executable, str(EXEMPTION_PY), "ids", "--log", str(cap)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    keys = [ln for ln in result.stdout.splitlines() if ln and not ln.startswith("ci-exemption:")]
    return "\n".join(keys), result.returncode


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_incomplete_leg_without_the_marker_contributes_nothing(tmp_path: Path) -> None:
    """The MEASURED gap: the old prose line yields ids=0 — the leg vanishes.

    `(no FAILED/ERROR nodeids)` prints bytes into the job log, and the rail's
    parser reads none of them as a failure. An entire leg drops out of the
    #3467 failure set and the comparison stays green over a set that never saw
    it: the vacuous pass.
    """
    keys, rc = _rail_ids(_capture("(no FAILED/ERROR nodeids)"), tmp_path)
    assert rc == 0
    assert keys == "", f"a silent leg must yield no key — measured, not assumed; got: {keys!r}"


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_incomplete_marker_becomes_a_named_attributable_failure(tmp_path: Path) -> None:
    """With the marker the SAME leg is a named, attributable failure key.

    This is the falsifiability the issue asks for: the unobservable leg stops
    being a silent omission and becomes a key the #3467 bar must account for.
    """
    keys, rc = _rail_ids(_capture(INCOMPLETE_MARKER), tmp_path)
    assert rc == 0
    assert "test-leg-INCOMPLETE-no-pytest-summary-failure-set-unobservable" in keys, keys


# ── 3. coverage of the workflow's legs (structural) ────────────────────────
#
# WHY A TEXT CHECK HERE, when this repo verifies invocation by EXECUTION
# (#4494): this asserts no SEMANTICS of the call — it asserts that the reporter
# is present at EVERY leg that prints a `/tmp/pytest.log` tail. The defect is
# per-leg, so a newly added leg that forgot the call would silently restore the
# blind tail at that site. (Whether the call is well-formed is the script's own
# tests' business, above.)


def test_every_pytest_log_tail_is_followed_by_the_reporter() -> None:
    """EVERY `/tmp/*.log` tail in these workflows reports through the reporter.

    Eight sites today: 5 `pytest.log` tails, 1 `trackb.log` (the must-pass Track B
    member, whose log name does not contain "pytest" — it was the coverage gap a
    first cut missed), 1 `pmv-pytest.log`, and 1 `pytest-d14.log`, which is written
    through `| tee` rather than a tail (this file counts both shapes; the `tee`
    site is the one a tail-only counter missed). The filter is
    deliberately the LOG TAIL, not the file name: a leg's log name is not a
    contract, and the rail's capture is `gh run view --log-failed` across every
    job, so any of these legs can omit its own failure set.
    """
    tails = 0
    for workflow in ATTRIBUTION_WORKFLOWS:
        lines = workflow.read_text().splitlines()
        for i, ln in enumerate(lines):
            # `tail -n 300 <log>` AND `tail -25 <log>` — the flag is spelled
            # both ways in these workflows, so match the shape, not one spelling.
            m = re.match(r"^tail\s+-n?\s*\d+\s+(\S+)$", ln.strip())
            # A leg may persist its output through `| tee /tmp/x.log` instead of a
            # tail; both are "this leg keeps a log", and both owe the reporter a call.
            m_tee = re.search(r"\|\s*tee\s+(/tmp/[^\s|]+\.log)", ln.strip())
            if m_tee:
                target = m_tee.group(1)
            elif m:
                target = m.group(1)
            else:
                continue
            if not (target.startswith("/tmp/") and target.endswith(".log")):
                continue
            tails += 1
            window = "\n".join(lines[i + 1 : i + 14])
            assert f"pytest-attribution.sh --log {target}" in window, (
                f"{workflow.name}:{i + 1}: the `{target}` tail is not followed by the "
                "reporter — this leg can silently omit its own failure set (#3470)"
            )
    # The measured inventory is 8: five `/tmp/pytest.log` tails in python-ci.yml —
    # `test`'s last-failed pre-phase (200), `test` (300), `test-slow` (300),
    # `test-concurrency-falkor` (120) and `test-carve-out` (200) — plus `trackb.log`,
    # plus `pmv-pytest.log` in post-merge-validation.yml, plus `pytest-d14.log` written
    # through `| tee`. The floor used to be 6 — TWO BELOW reality (8 sites exist at
    # main) — so a single leg could be renamed out from under this check and it
    # stayed green; two of them could go before the floor even noticed.
    # A drop here means a leg was lost, not that it got safer; an ADDITION is fine,
    # which is why this is a floor.
    assert tails >= 8, f"only {tails} pytest-log sites found (expected >= 8) — a leg was renamed or dropped"
