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
   This layer is HOST-ONLY: the rail is agent-infra's, reached through the
   machine-local `scripts` pointer (#9475 — gitignored, not tracked, absent until
   `tools/link_agent_infra_scripts.py` materialises it), so both of its cases skip by
   name where the rail is absent (see ``RAIL_AVAILABLE``) — they never fail on that
   environment.

No DB, no network, no Docker: every input is a file this module writes to
``tmp_path``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "pytest-attribution.sh"
EXEMPTION_PY = REPO / "scripts" / "ci_exemption.py"
WORKFLOW = REPO / ".github" / "workflows" / "python-ci.yml"
CI_WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
#: The SCOPE of this guard, named rather than implied: these two workflows and no
#: others. The list is not "every workflow in the repo", and saying so matters —
#: a workflow outside it is INVISIBLE here, and its leg can silently omit its own
#: failure set (#3470). The scope is deliberate and its one known omission is
#: recorded below: `.github/workflows/ci.yml`'s `hosted-e2e` leg captures
#: `/tmp/hosted-e2e.log` through `| tee` and has no reporter call, which is #9300.
#: #9300 is its own change (wire the leg in `ci.yml`, add it here, raise the floor
#: 8 → 9, and add its id to `test_a_failing_pytest_still_reds_the_step`'s cases).
#: The deferral is scope, not permission: THIS PR does change
#: `.github/workflows/python-ci.yml` (the `runs-on` pin and the D14 re-raise), so no
#: credential or workflow-scope limit stands behind #9300 — only the decision to keep
#: the runner-label fix reviewable on its own terms.
ATTRIBUTION_WORKFLOWS = [
    WORKFLOW,
    REPO / ".github" / "workflows" / "post-merge-validation.yml",
]


def test_the_uncovered_workflow_is_named_not_silently_dropped() -> None:
    """The KNOWN gap is pinned, so closing it cannot be mistaken for tidying.

    A coverage floor is only honest about the surface it names. `ci.yml`'s
    `hosted-e2e` leg writes its only log through `| tee /tmp/hosted-e2e.log` and has
    no reporter, so it is outside this guard's scope by decision and by record
    (#9300) — not by accident. When the leg is wired, this test must go and the
    workflow must be added above: the failure here is the instruction to do both.
    """
    body = CI_WORKFLOW.read_text()
    assert "| tee /tmp/hosted-e2e.log" in body, (
        "ci.yml no longer captures the hosted-e2e log the way #9300 describes — "
        "re-check that issue before widening or keeping this guard's scope"
    )
    assert CI_WORKFLOW not in ATTRIBUTION_WORKFLOWS, (
        "ci.yml is now in ATTRIBUTION_WORKFLOWS — delete this test, raise the coverage "
        "floor to the new inventory, and add the hosted-e2e case to "
        "test_a_failing_pytest_still_reds_the_step (#9300)"
    )


# The RAIL this module measures against lives in agent-infra, not here: `scripts` is a
# MACHINE-LOCAL pointer materialised from `$AGENT_INFRA_PATH` by
# `tools/link_agent_infra_scripts.py` (#9475 — it is gitignored and NOT tracked, because a
# git symlink blob cannot hold a variable), so `EXEMPTION_PY` resolves on a developer host
# whose pointer has been materialised and is ABSENT on a runner. python-ci.yml is
# self-contained BY DECISION (fix #555: "no reusable-workflow call and no
# `scripts`/`agent-infra` symlinks — both resolve to paths that are broken on the runner") and
# no CI job checks out agent-infra, so the rail layer can only be EXECUTED where the rail
# exists. Off that host the two rail cases SKIP BY NAME rather than fail on an environment
# this repo does not own — and the skip is announced, so a green leg reads "the marker
# contract is pinned, the rail's parse was not run", never the vacuous "the rail agreed" that
# the marker itself exists to prevent. The marker TEXT stays pinned repo-side on EVERY run, by
# `test_marker_text_is_exactly_the_issue_contract`.
RAIL_AVAILABLE = EXEMPTION_PY.exists()
RAIL_ABSENT_REASON = (
    "agent-infra rail not checked out: `scripts/ci_exemption.py` is reached through the "
    "machine-local `scripts` pointer (#9475 — gitignored, not tracked, so it is absent until "
    "`uv run python tools/link_agent_infra_scripts.py` materialises it from "
    "$AGENT_INFRA_PATH), which is why it is missing off a developer host (python-ci.yml is "
    "self-contained by decision, fix #555): the rail's parse of the marker can only be "
    "executed where agent-infra is present"
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
        b"============================= test session starts ==============================\n"
        b"collected 3 items\n\n"
        b"tests/test_event_store.py::test_seq_is_monotonic_under_concurrency_live_falkor PASSED [ 33%]\n"
        b"tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes PASSED [100%]\n\n"
        b"============================== 3 passed in 51.08s ==============================\n"
    ) + b"\x00" * 4096
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


# The measured artifact: the leg's own session opens, starts two tests, and then a
# NESTED pytest run (a test that shells out to pytest) prints its own bannered
# session, its own `ERROR <path>` record and its own bannered summary. The leg's own
# summary never prints — it died mid-test.
NESTED_COLLECTION_ERROR_LOG = (
    "==================================== test session starts ====================================\n"
    "platform darwin -- Python 3.12.0, pytest-9.1.1\n"
    "collected 40 items\n"
    "\n"
    "tests/test_a.py::test_one PASSED [ 10%]\n"
    "tests/test_a.py::test_two \n"
    "==================================== test session starts ====================================\n"
    "collected 1 item\n"
    "\n"
    "ERROR tests/nested_bad.py - ImportError while importing test module\n"
    "!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
    "=============================== 1 error in 0.09s ===============================\n"
)


@pytest.mark.parametrize("rc", ["137", "2", None])
def test_a_nested_collection_error_cannot_silence_a_killed_legs_marker(
    tmp_path: Path, rc: str | None
) -> None:
    """A nested child's collection error must never switch this leg's marker off.

    A nested pytest run's raw stdout lands in the leg's log (the measured artifact
    carries a whole nested session), so a child that hits a collection error would
    otherwise restore exactly the "leg contributes nothing to the #3467 set" defect
    this file exists for: the summary is labelled STALE and then the whole leg
    silently yields no marker and no culprit.

    ALL THREE ways a leg like this can arrive are exercised, because each one reaches
    the suppression by a different route and one of them does not involve an exit code
    at all:

    * ``137`` — the SIGKILL that follows the watchdog's SIGINT;
    * ``2`` — pytest's own INTERRUPTED code, which is ALSO its collection-error code,
      so the log's collection signature is what decides it; `COLLECTION_VERDICT` reads
      the LAST summary whatever session owns it, so it must not be believed alone;
    * ``None`` — the documented triager path (`--rc` is optional and a hand-run
      against a downloaded log has no exit code): here `KILLED=0`, so nothing but the
      ownership of the summary stands between this leg and a false clean.

    The in-flight name is asserted, not just the marker: a marker that names no test
    is the "kill that names nothing" this section was added for.
    """
    log = tmp_path / "pytest.log"
    log.write_text(NESTED_COLLECTION_ERROR_LOG)
    result = run_script(log, rc=rc)
    assert INCOMPLETE_MARKER in result.stdout, result.stdout
    assert in_flight_lines(result.stdout) == ["tests/test_a.py::test_two"], result.stdout
    # The child's summary is named as foreign, so the reader is not left wondering
    # why a leg with `1 error in 0.09s` in its log got an INCOMPLETE marker.
    assert "STALE session summary" in result.stdout, result.stdout
    # and the KILL/INCOMPLETE explanation, not the collection-error one (a leg with no
    # verdict of its own is never "already attributable", whatever else the log carries)
    assert "COLLECTION ERROR" not in result.stdout, result.stdout


def test_a_legs_own_collection_error_still_suppresses_the_marker(tmp_path: Path) -> None:
    """The control for the test above: ownership is what licenses the suppression.

    Identical collection signature, identical `ERROR <path>` record — but this time the
    summary closes the LEG's own session (depth 0 at EOF, no start line after it), so
    the leg IS attributable and the marker must stay off. Without this half, gating the
    suppression on ownership could be implemented as "never suppress" and both the
    nested test and a whole-leg regression would read the same.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "==================================== test session starts ====================================\n"
        "collected 1 item\n"
        "\n"
        "ERROR tests/test_bad.py - ImportError while importing test module\n"
        "!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!\n"
        "=============================== 1 error in 0.09s ===============================\n"
    )
    result = run_script(log, rc="2")
    assert INCOMPLETE_MARKER not in result.stdout, result.stdout
    assert "STALE session summary" not in result.stdout, result.stdout


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


# ── 1c. a killed leg states its FAILURE SET from its own junitxml (#10914) ──
#
# A leg killed at its watchdog never reaches `-r fEs`, so its LOG carries no
# `FAILED <nodeid>` record: the failing testcase appears only as a `-v` progress
# line. Measured on run 42050189058, job `test (f)` (907 passed, **1 failed**,
# killed at its 26m budget): `ci_exemption.py ids` read ZERO ids from that log, so
# the #9984 predicate fell back to `job-unreadable::test-f` and the merge was
# BLOCKED — while the leg's own `--junitxml` held the nodeid AND its exception type
# on disk. The record existed; nothing read it.
#
# These tests pin the three properties that make reading it safe:
#   1. the emission is pytest's OWN `-r fEs` shape, so the rail derives the same id
#      and the same signature a completed run would (pinned against the rail itself
#      in §2b — the equivalence is EXECUTED, not asserted);
#   2. a leg that owns a verdict never re-emits — one failure, one source;
#   3. an absent/unparseable artifact is NAMED as unknown and emits no id, so a
#      missing junitxml can never read as "no test failed".

#: A killed leg's log: pytest's `-v` progress stream, no session summary, and no
#: `FAILED`/`ERROR` record — the shape the rail reads as `ids=0`.
KILLED_LEG_LOG = (
    "==================== test session starts ====================\n"
    "platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.5.0\n"
    "plugins: xdist-3.8.0, timeout-2.4.0\n"
    "timeout: 300.0s\ntimeout method: signal\n"
    "created: 4/4 workers\n"
    "tests/test_capture_cost_measurement.py::test_a_cost_row_is_emitted PASSED [ 17%]\n"
    "tests/test_capture_cost_measurement.py::test_emission_write_is_handed_off_the_event_loop"
    " FAILED [ 18%]\n"
    # Started, never resolved: the kill landed here (the in-flight report's subject).
    "tests/test_capture_phase_d_dedup.py::test_capture_promotes_a_new_operator\n"
)

#: The junitxml that leg wrote — pytest's own xunit1 output, COPY-EDITED FROM A REAL
#: RUN of each failure shape (see `test_the_emitted_detail_matches_pytest_s_own_block`
#: for the matching `-r fEs`/FAILURES rendering each one is compared against).
JUNIT_KILLED_LEG = """<?xml version="1.0" encoding="utf-8"?>\
<testsuites name="pytest tests"><testsuite name="pytest" errors="0" failures="3" \
skipped="0" tests="4" time="1570.0">\
<testcase classname="tests.test_capture_cost_measurement" \
name="test_emission_write_is_handed_off_the_event_loop" \
file="tests/test_capture_cost_measurement.py" line="3097" time="300.0">\
<failure message="Failed: Timeout (&gt;300.0s) from pytest-timeout.">\
def test_emission_write_is_handed_off_the_event_loop():
&gt;       resp = _b7_capture_client.post("/v1/sessions", json={...})
E       Failed: Timeout (&gt;300.0s) from pytest-timeout.

tests/test_capture_cost_measurement.py:3143: Failed</failure></testcase>\
<testcase classname="tests.test_capture_cost_measurement" name="test_a_cost_row_is_emitted" \
file="tests/test_capture_cost_measurement.py" line="1428" time="0.1" />\
<testcase classname="tests.test_x" name="test_shape[2]" file="tests/test_x.py" line="9" \
time="0.1"><failure message="assert 2 == 1">v = 2

    @pytest.mark.parametrize("v", [1, 2])
    def test_shape(v):
&gt;       assert v == 1
E       assert 2 == 1

tests/test_x.py:12: AssertionError</failure></testcase>\
<testcase classname="tests.test_x.TestShape" name="test_class_leaf" file="tests/test_x.py" \
line="19" time="0.1"><failure message="assert 3 == 1">    def test_class_leaf(self):
&gt;       assert 3 == 1
E       assert 3 == 1

tests/test_x.py:20: AssertionError</failure></testcase>\
</testsuite></testsuites>
"""

EMITTED_TIMEOUT = (
    "FAILED tests/test_capture_cost_measurement.py::"
    "test_emission_write_is_handed_off_the_event_loop"
    " - Failed: Timeout (>300.0s) from pytest-timeout."
)
EMITTED_ASSERTION = "FAILED tests/test_x.py::test_shape[2] - AssertionError: assert 2 == 1"
#: The CLASS-based failure. `testcase/@name` is the leaf only, so this id is only
#: reconstructable from `@classname` — `file::name` would name `TestShape`'s sibling
#: as if it were a module-level test (i.e. a nodeid pytest never wrote).
EMITTED_CLASS_LEAF = (
    "FAILED tests/test_x.py::TestShape::test_class_leaf - AssertionError: assert 3 == 1")


def _killed_leg(tmp_path: Path, junit: str | None = JUNIT_KILLED_LEG) -> tuple[Path, Path]:
    log = tmp_path / "pytest.log"
    log.write_text(KILLED_LEG_LOG, encoding="utf-8")
    path = tmp_path / "junit.xml"
    if junit is not None:
        path.write_text(junit, encoding="utf-8")
    return log, path


def _reported_failures(stdout: str) -> list[str]:
    """The `FAILED`/`ERROR` records the reporter itself emitted (not its prose)."""
    return [ln for ln in stdout.splitlines() if re.match(r"^(FAILED|ERROR) ", ln)]


def test_a_killed_leg_states_its_failing_testcases_from_its_own_junitxml(
        tmp_path: Path) -> None:
    """The measured gap: the leg's own artifact names what its log could not.

    Both failures are reported — the parametrized id keeps its `[2]` suffix (a
    truncated id is a DIFFERENT test, and the comparable database keys on it) — and
    each carries pytest's `<ExceptionType>: <E-line>` shape, which is what gives the
    rail a signature to compare against main instead of an unsigned row.
    """
    log, junit = _killed_leg(tmp_path)
    result = run_script(log, "--junit", str(junit), rc="137")
    assert result.returncode == 0, result.stderr
    assert "INCOMPLETE" in result.stdout, "the leg must still be named unobservable"
    assert _reported_failures(result.stdout) == [
        EMITTED_TIMEOUT, EMITTED_ASSERTION, EMITTED_CLASS_LEAF], (
        f"the reporter did not state the leg's own failing testcases:\n{result.stdout}")


def test_a_completed_leg_never_re_emits_its_failures_from_the_junitxml(
        tmp_path: Path) -> None:
    """One failure, ONE source: a leg that owns a summary already published them.

    Re-emitting would put the same id in the log twice with a second provenance — a
    signature that could disagree with the block the rail prefers.
    """
    log = tmp_path / "pytest.log"
    log.write_text(
        "==================== test session starts ====================\n"
        "tests/test_x.py::test_shape[2] FAILED [ 50%]\n"
        "==================== short test summary info ====================\n"
        "FAILED tests/test_x.py::test_shape[2] - assert 2 == 1\n"
        "= 1 failed in 0.50s =\n",
        encoding="utf-8")
    junit = tmp_path / "junit.xml"
    junit.write_text(JUNIT_KILLED_LEG, encoding="utf-8")
    result = run_script(log, "--junit", str(junit), rc="1")
    assert result.returncode == 0, result.stderr
    assert _reported_failures(result.stdout) == [], (
        f"a completed leg re-emitted its own failure records:\n{result.stdout}")


@pytest.mark.parametrize(
    ("junit_state", "expected"),
    [
        ("missing", "does not exist"),
        ("empty", "holds no failing testcase"),
        ("unparseable", "could not be parsed"),
    ],
)
def test_an_unreadable_junitxml_is_named_unknown_never_an_empty_failure_set(
        tmp_path: Path, junit_state: str, expected: str) -> None:
    """Fail CLOSED: no artifact is UNKNOWN, and UNKNOWN is not "nothing failed".

    The interesting failure is silent: a reporter that emitted nothing at all here
    would be indistinguishable from a clean leg, which is the class this file exists
    to prevent.
    """
    junit_text = {
        "missing": None,
        "empty": '<?xml version="1.0"?><testsuites><testsuite /></testsuites>',
        "unparseable": "<testsuites><testsuite><testcase ",
    }[junit_state]
    log, junit = _killed_leg(tmp_path, junit=junit_text)
    result = run_script(log, "--junit", str(junit), rc="137")
    assert result.returncode == 0, result.stderr
    assert _reported_failures(result.stdout) == [], (
        f"an unreadable junitxml produced a failure id:\n{result.stdout}")
    assert expected in result.stdout, (
        f"the {junit_state} artifact was not NAMED (expected {expected!r}):\n"
        f"{result.stdout}")


def test_a_leg_that_wrote_no_junitxml_states_no_test_level_identity(
        tmp_path: Path) -> None:
    """`--junit` is optional, and its absence is stated — never silently empty."""
    log, _ = _killed_leg(tmp_path, junit=None)
    result = run_script(log, rc="137")
    assert result.returncode == 0, result.stderr
    assert _reported_failures(result.stdout) == [], result.stdout
    assert "no --junit was passed" in result.stdout, result.stdout


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


# ── 2b. the emitted record IS the rail's record — measured, not asserted ────
#
# The point of emitting from the junitxml is not "the log now names a test". It is
# that the rail can COMPARE that name against main, which needs the same id AND the
# same signature a completed run produces for the same failure. Below: the same two
# failures rendered both ways — as pytest renders them on a completed run (FAILURES
# block + `-r fEs`), and as this reporter renders them from the killed leg's
# junitxml — through the SAME two doors, compared for equality. If a future edit
# changes the emission shape (drops the `<Type>: ` prefix, truncates a parametrized
# suffix), the two sides stop matching here.

#: The SAME failures as `JUNIT_KILLED_LEG`, in the shape a COMPLETED run writes: the
#: FAILURES block (where the rail takes the exception TYPE from, via pytest's
#: `path.py:LINE: ExceptionType` trailer) and the `-r fEs` short summary.
COMPLETED_LEG_LOG = """\
___________________ test_emission_write_is_handed_off_the_event_loop ___________________
    def test_emission_write_is_handed_off_the_event_loop():
>       resp = _b7_capture_client.post("/v1/sessions", json={...})
E       Failed: Timeout (>300.0s) from pytest-timeout.

tests/test_capture_cost_measurement.py:3143: Failed
_____________________________ test_shape[2] ______________________________
    @pytest.mark.parametrize("v", [1, 2])
    def test_shape(v):
>       assert v == 1
E       assert 2 == 1

tests/test_x.py:12: AssertionError
_____________________________ test_class_leaf ______________________________
    def test_class_leaf(self):
>       assert 3 == 1
E       assert 3 == 1

tests/test_x.py:20: AssertionError
=========================== short test summary info ============================
FAILED tests/test_capture_cost_measurement.py::test_emission_write_is_handed_off_the_event_loop - Failed: Timeout (>300.0s) from ...
FAILED tests/test_x.py::test_shape[2] - assert 2 == 1
FAILED tests/test_x.py::TestShape::test_class_leaf - assert 3 == 1
===================== 3 failed in 300.4s ======================
"""


def _rail_signatures(text: str, tmp_path: Path) -> dict[str, set[str]]:
    """`ci_exemption.py signatures` over a de-stamped capture, as nodeid -> signatures.

    De-stamped on purpose: the #9984 predicate removes the per-line transport stamp
    (`strip_transport_stamps`) before EITHER door reads the per-job log, and the
    signature door's `_LOG_PREFIX_RE` requires a tab before a timestamp — so a
    stamped line never matches its `^FAILED` anchor (#10202 review 4).
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    cap = tmp_path / "capture-signatures.txt"
    cap.write_text(text)
    result = subprocess.run(
        [sys.executable, str(EXEMPTION_PY), "signatures", "--log", str(cap)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    out: dict[str, set[str]] = {}
    for line in result.stdout.splitlines():
        if not line or line.startswith("ci-exemption:"):
            continue
        nodeid, _, sig = line.partition("\t")
        if nodeid.strip():
            out.setdefault(nodeid.strip(), set()).add(sig.strip())
    return out


@pytest.mark.skipif(not RAIL_AVAILABLE, reason=RAIL_ABSENT_REASON)
def test_the_junit_emission_reaches_the_rail_as_the_same_failure(tmp_path: Path) -> None:
    """Both renderings of one failure must land on the SAME id and signature."""
    log, junit = _killed_leg(tmp_path)
    killed = run_script(log, "--junit", str(junit), rc="137")
    assert killed.returncode == 0, killed.stderr
    emitted = _rail_signatures(killed.stdout, tmp_path / "killed")
    completed = _rail_signatures(COMPLETED_LEG_LOG, tmp_path / "completed")
    assert emitted, (
        "the rail read NO id from the reporter's own output — the emission is "
        f"unattributable, which is the whole defect:\n{killed.stdout}")
    assert emitted == completed, (
        "the killed leg's junitxml does not reproduce the failure a completed run "
        f"records for the same test — the rows are not comparable:\n"
        f"emitted:   {emitted}\ncompleted: {completed}")
    # And the ids are the real ones, suffix and all.
    assert set(emitted) == {
        "tests/test_capture_cost_measurement.py::"
        "test_emission_write_is_handed_off_the_event_loop",
        "tests/test_x.py::test_shape[2]",
        "tests/test_x.py::TestShape::test_class_leaf",
    }, emitted


#: Two failing tests that share a LEAF name inside one file, in two different
#: classes — the shape `file::name` cannot represent. Pre-fix the reporter emitted
#: `tests/test_x.py::test_class_leaf` TWICE: one id, so one of the two failures
#: silently disappeared from the reported set.
JUNIT_SAME_LEAF_TWO_CLASSES = """<?xml version="1.0" encoding="utf-8"?>\
<testsuites name="pytest tests"><testsuite name="pytest" errors="0" failures="2" \
skipped="0" tests="2" time="1.0">\
<testcase classname="tests.test_x.TestAlpha" name="test_leaf" file="tests/test_x.py" \
line="9" time="0.1"><failure message="assert 1 == 2">E       assert 1 == 2\n\ntests/test_x.py:10: AssertionError</failure></testcase>\
<testcase classname="tests.test_x.TestBeta" name="test_leaf" file="tests/test_x.py" \
line="19" time="0.1"><failure message="assert 3 == 4">E       assert 3 == 4\n\ntests/test_x.py:20: AssertionError</failure></testcase>\
</testsuite></testsuites>
"""


def test_two_classes_sharing_a_leaf_name_keep_DISTINCT_nodeids(tmp_path: Path) -> None:
    """The collapse arm: `file::name` makes two failures one id.

    Both failures must reach the rail as the ids pytest itself recorded. Pre-fix the
    reporter emitted the SAME line twice (`tests/test_x.py::test_leaf`), so the rail
    saw one failure and the other vanished from the reported set — the failure set
    `ci_exemption.py signatures` then compares against main is short by one.
    """
    log, junit = _killed_leg(tmp_path, junit=JUNIT_SAME_LEAF_TWO_CLASSES)
    result = run_script(log, "--junit", str(junit), rc="137")
    assert result.returncode == 0, result.stderr
    assert _reported_failures(result.stdout) == [
        "FAILED tests/test_x.py::TestAlpha::test_leaf - AssertionError: assert 1 == 2",
        "FAILED tests/test_x.py::TestBeta::test_leaf - AssertionError: assert 3 == 4",
    ], f"the class segment was dropped, collapsing two failures onto one id:\n{result.stdout}"
    assert len(set(_reported_failures(result.stdout))) == 2, result.stdout


def test_a_classname_naming_another_module_is_NOT_attributed_to_this_file(
        tmp_path: Path) -> None:
    """The honest arm: when the class chain cannot be attributed, say so.

    `classname` and `file` disagree about the module, so the trailing capitalised
    components belong to some other nodeid. Emitting `file::name` here would be an
    invented plausible id — the docstring's own rule is to name the gap instead.
    """
    mismatched = JUNIT_KILLED_LEG.replace(
        'classname="tests.test_x.TestShape" name="test_class_leaf"',
        'classname="tests.test_other.TestShape" name="test_class_leaf"')
    log, junit = _killed_leg(tmp_path, junit=mismatched)
    result = run_script(log, "--junit", str(junit), rc="137")
    assert result.returncode == 0, result.stderr
    assert not [ln for ln in _reported_failures(result.stdout) if "test_class_leaf" in ln], (
        f"a mismatched classname produced a fabricated nodeid:\n{result.stdout}")
    assert "does not match" in result.stdout, result.stdout


# ── 2c. the wiring: a leg that WRITES a junitxml must PASS it ──────────────
#
# The emission above is worthless if the call site forgets `--junit`: the leg falls
# straight back to stating nothing, which is the `ids=0` → `job-unreadable::` →
# BLOCK path this change closes (#10914). So the pairing is derived from the
# workflow rather than remembered: for EVERY reporter call, the pytest invocation it
# reports on is located, and when that invocation writes `--junitxml=`, the call
# must name the same artifact with `--junit`.
#
# It is a REQUIRED pairing, not a suggestion, only where the artifact exists — the
# `test-concurrency-falkor` leg runs a fixed nodeid list with no `--junitxml` at all,
# so it is correctly exempt by construction (not by an allow-list that would rot).

_JUNITXML_ARG_RE = re.compile(r"--junitxml=(\"[^\"]+\"|\S+)")


def _joined_command(lines: list[str], start: int) -> str:
    """A bash command as written: the line at `start` plus its `\\` continuations."""
    parts = [lines[start]]
    i = start
    while parts[-1].rstrip().endswith("\\") and i + 1 < len(lines):
        i += 1
        parts.append(lines[i])
    return "\n".join(parts)


def test_every_leg_that_writes_a_junitxml_passes_it_to_the_reporter() -> None:
    lines = WORKFLOW.read_text().splitlines()
    # A commented-out invocation is not a site; `# ... python -m pytest` in the prose
    # above every leg is common in this file and must not be counted.
    pytest_sites = [
        i for i, ln in enumerate(lines)
        if re.search(r"\bpython\s+-m\s+pytest\b", ln) and not ln.strip().startswith("#")
    ]
    checked = 0
    for site, line in enumerate(lines):
        if "bash .github/scripts/pytest-attribution.sh" not in line:
            continue
        assert not line.strip().startswith("#"), (
            f"python-ci.yml:{site + 1}: the reporter call is COMMENTED OUT:\n{line}")
        call = _joined_command(lines, site)
        prior = [i for i in pytest_sites if i < site]
        assert prior, (
            f"python-ci.yml:{site + 1}: no `python -m pytest` invocation above this "
            "reporter call — this guard no longer knows what the call reports on")
        invocation = _joined_command(lines, prior[-1])
        junit = _JUNITXML_ARG_RE.search(invocation)
        if junit is None:
            # The leg keeps no junitxml: there is no artifact to name (#10914's
            # concurrency leg). Nothing to require.
            continue
        checked += 1
        assert "--junit" in call, (
            f"python-ci.yml:{site + 1}: this leg writes {junit.group(0)} but does not "
            "pass it to the reporter — a kill at its watchdog then states NO failing "
            "testcase, which is the `ids=0` → `job-unreadable::` → BLOCK defect this "
            f"change closes (#10914):\n{call}")
    assert checked >= 6, (
        f"only {checked} reporter call(s) in python-ci.yml were checked for the "
        "--junit pairing — the legs that write a junitxml are not the ones this "
        "guard just inspected, so it has gone vacuous (raise/lower this floor with "
        "the leg inventory, never to make a red go away)")


# ── 3. coverage of the workflow's legs (structural) ────────────────────────
#
# WHY A TEXT CHECK HERE, when this repo verifies invocation by EXECUTION
# (#4494): this asserts no SEMANTICS of the call — it asserts that the reporter
# is present at EVERY leg that prints a `/tmp/pytest.log` tail. The defect is
# per-leg, so a newly added leg that forgot the call would silently restore the
# blind tail at that site. (Whether the call is well-formed is the script's own
# tests' business, above.)


#: THE INVENTORY SPLIT BELOW IS DERIVED, NOT RESTATED (2026-10-29, #10244 review). The
#: prose in `test_every_pytest_log_tail_is_followed_by_the_reporter` used to carry the
#: split by hand, and it drifted the moment `test-slow` migrated: the comment still
#: claimed 2 per-runner sites and 3 `/tmp` literals after the migration made it 3 and
#: 2, inside the very function the next `/tmp` migrator reads. These two numbers are
#: asserted against the workflows by `test_the_stated_inventory_matches_the_workflows`,
#: so a further migration reds the guard instead of silently falsifying its map.
_STATED_PER_RUNNER_PYTEST_LOG_SITES = 6
_STATED_LITERAL_PYTEST_LOG_SITES = 0

#: `tail -n 300 <log>` AND `tail -25 <log>` — the flag is spelled both ways in these
#: workflows, so match the SHAPE, not one spelling. Anchored (`^tail`), so a comment
#: that merely mentions the spelling cannot be counted as a site.
#:
#: ⛔ A LEG'S LOG MAY BE A PER-RUNNER PATH, NOT A `/tmp` LITERAL (2026-10-25). The
#: `test` job writes through `$PYTEST_LOG` (= `${{ runner.temp }}/pytest.log`) because a
#: `/tmp/pytest.log` literal is SHARED between concurrent jobs on a self-hosted runner,
#: which made the kill's own counts and tail describe another shard. The capture is the
#: same capture (and owes the same reporter call), so the SITE is matched on either
#: spelling — the alternative was a guard that reports "a leg was dropped" when a path
#: was hardened, which is exactly the false signal this floor must not emit.
_TAIL_SITE_RE = re.compile(r"^tail\s+-n?\s*\d+\s+(\S+)$")
#: The per-runner indirection a hardened job writes through, as it appears in a `tail`.
_RUNNER_TEMP_LOG = "\"$PYTEST_LOG\""
#: The other per-runner spelling: a job whose log lives in its OWN
#: `${RUNNER_TEMP}` — #7815 moved `test-d14-hosted-api` there, because a
#: `/tmp` literal is shared between concurrent jobs on a self-hosted runner.
#: Later migrations moved other legs onto the `"$PYTEST_LOG"` indirection instead
#: (resolved once per job, fail-closed on `${RUNNER_TEMP:?}`), so this spelling now
#: covers `test-d14-hosted-api` alone. The branch is kept because that leg is a real
#: site and a guard whose subject is this drift must not be spelling-dependent — and
#: NO COUNT is stated here on purpose: the split has moved twice, and a count in a
#: comment is not derived by any pin, so it goes stale silently. The pins that DO
#: derive numbers are `_STATED_PER_RUNNER_PYTEST_LOG_SITES` /
#: `_STATED_LITERAL_PYTEST_LOG_SITES` and `test_the_stated_inventory_matches_the_workflows`;
#: read those, never this sentence, for how many legs use which spelling.
#: Quoting is preserved from the line it came from, so the `--log <token>` pairing
#: below compares like with like.
_RUNNER_TEMP_TMPL_RE = re.compile(
    r"^\"?\$\{RUNNER_TEMP:-/tmp\}/[^\"\s|]+\.(?:log|xml)\"?$")
#: A leg may persist its output through `| tee /tmp/x.log` instead of a tail; both are
#: "this leg keeps a log", so both owe the reporter a call. This one has to SEARCH a
#: longer command line (the capture sits mid-pipeline, followed by `|| rc=$?`), so it
#: is not end-anchored — which is why comment lines are filtered out before matching.
_TEE_SITE_RE = re.compile(
    r"\|\s*tee\s+(\"?\$\{RUNNER_TEMP:-/tmp\}/[^\s|]+\.log\"?|/tmp/[^\s|]+\.log)")


def capture_site(line: str) -> str | None:
    """The pytest log this workflow line captures, or None if it captures none.

    Comment lines are skipped BEFORE matching. The `tail` branch is anchored, so it
    cannot count prose — but the `tee` branch cannot be end-anchored (the capture sits
    mid-pipeline), and an unanchored search over an unfiltered line also matches
    DOCUMENTATION: the D14 site is spelled `tee /tmp/pytest-d14.log` verbatim in this
    PR's description and in `tests/test_markers.py`, so the moment a sentence
    describing it lands in the comment block above the site, the counter reads one
    MORE than the real sites (a count of 9 against the 8 real sites the floor pins). A
    phantom SITE is the dangerous direction: the assertion below is a FLOOR, so one
    phantom silently re-buys the slack the floor raise exists to remove — and the
    reporter-window check passes for a comment sitting above the real call.
    """
    stripped = line.strip()
    if stripped.startswith("#"):
        return None
    match = _TEE_SITE_RE.search(stripped) or _TAIL_SITE_RE.match(stripped)
    if match is None:
        return None
    target = match.group(1)
    if target == _RUNNER_TEMP_LOG:
        # A per-runner path: the leg still owes the reporter a call, and the
        # window assertion below compares this same token (the call is
        # `--log "$PYTEST_LOG"`), so the pairing is checked exactly as it is for
        # a literal `/tmp/*.log`.
        return target
    if _RUNNER_TEMP_TMPL_RE.match(target):
        # The `${RUNNER_TEMP:-/tmp}/<name>` spelling: ALSO per-runner. #7815 moved the
        # hardened jobs onto it; later migrations moved most of them onto the
        # `"$PYTEST_LOG"` indirection instead, so today this spelling covers
        # `test-d14-hosted-api` ALONE. The branch stays because that leg is a real site
        # and a guard whose subject is this drift must not be spelling-dependent — and
        # no count is stated here, for the reason given on `_RUNNER_TEMP_TMPL_RE` above.
        # Returned verbatim so the `--log <token>` pairing is the same string comparison
        # as the literal case.
        return target
    if not (target.startswith("/tmp/") and target.endswith(".log")):
        return None
    return target


def test_every_pytest_log_tail_is_followed_by_the_reporter() -> None:
    """EVERY pytest-log capture in these workflows reports through the reporter.

    A capture is a `/tmp/*.log` literal OR a per-runner path a hardened leg writes
    through (`"$PYTEST_LOG"`, or a job's own `${RUNNER_TEMP:-/tmp}/...`) — the shape is
    the LOG CAPTURE, not the path, so all of them owe the
    reporter. Eight sites in the two workflows this guard NAMES (`ATTRIBUTION_WORKFLOWS` —
    the scope is explicit, and its one known omission, ci.yml's `hosted-e2e` leg,
    is pinned by `test_the_uncovered_workflow_is_named_not_silently_dropped` and
    #9300): 6 `pytest.log`-named tails in python-ci.yml — 6 of them the per-runner
    `"$PYTEST_LOG"`, not a `/tmp` literal, see the DERIVED split pinned by
    `test_the_stated_inventory_matches_the_workflows` — plus `pmv-pytest.log` in
    post-merge-validation.yml, plus `pytest-d14.log`, written through `| tee` rather
    than a tail (this file counts both shapes; the `tee` site is the one a tail-only
    counter missed). `test-track-b`'s tail was the `trackb.log` site in an earlier
    inventory; #10280 migrated that leg onto `"$PYTEST_LOG"`, so its log no longer
    carries a distinct name and it is counted in the six. The filter is deliberately
    the LOG CAPTURE, not the file name: a leg's log name is not a contract, and the
    rail's capture is `gh run view --log-failed` across every job, so any of these legs
    can omit its own failure set.
    """
    tails = 0
    for workflow in ATTRIBUTION_WORKFLOWS:
        lines = workflow.read_text().splitlines()
        for i, ln in enumerate(lines):
            target = capture_site(ln)
            if target is None:
                continue
            tails += 1
            window = "\n".join(lines[i + 1 : i + 14])
            assert f"pytest-attribution.sh --log {target}" in window, (
                f"{workflow.name}:{i + 1}: the `{target}` capture is not followed by the "
                "reporter — this leg can silently omit its own failure set (#3470)"
            )
    # The measured inventory is 8: six `pytest.log`-named tails in python-ci.yml, ALL
    # through the per-runner `"$PYTEST_LOG"` — `test`'s last-failed pre-phase (200),
    # `test` (300), `test-slow` (300) (the fast job since #10028, `test-slow` since the
    # #10028 residue that PR closed), `test-concurrency-falkor` (120),
    # `test-carve-out` (200) and `test-track-b` (25) (those three since #10280, which
    # also renamed the leg's log from the host-global `/tmp/trackb.log`) — plus
    # `pmv-pytest.log` in post-merge-validation.yml, plus `pytest-d14.log` written
    # through `| tee`. The floor used to be 6 against the 7 sites main's tail-only
    # counter measured — ONE below reality — so a single leg could be renamed out from
    # under this check while it stayed green (it took two losses to red it). Counting
    # the `tee` site as well makes the inventory 8, so this floor has zero slack NOW,
    # over the surface it names: losing any one leg in these two workflows reds it.
    # A drop here means a leg was lost, not that it got safer; an ADDITION is fine,
    # which is why this is a floor.
    assert tails >= 8, f"only {tails} pytest-log sites found (expected >= 8) — a leg was renamed or dropped"


def test_the_stated_inventory_matches_the_workflows() -> None:
    """The inventory the floor above STATES must equal the inventory it MEASURES.

    The floor is a bound; the prose above it is a MAP — "which legs are still on a
    `/tmp/pytest.log` literal, and which moved to a per-runner path" — and
    it is the map the next incremental `/tmp` migration reads before it edits a job.
    A bound keeps being satisfied while the map goes wrong, which is how the pre-#10028
    split (2 per-runner, 3 literals) survived `test-slow`'s migration: the comment sat
    in the same function as the floor, said 2 and 3, and the truth was 3 and 2. The
    delivered state since #7815 is 6 per-runner and ZERO literals — the count that
    says the migration is complete rather than merely in progress. (It is SIX, not the
    five #7815 alone produced: #10280 renamed `test-track-b`'s `trackb.log` onto the same
    `"$PYTEST_LOG"` the other legs use, so that leg now carries the common name and is
    counted here rather than named separately.)

    The numbers are DERIVED here from `capture_site` — the same reading the floor uses —
    rather than re-read out of the prose, so this cannot become a tautology. Stated
    counts live in `_STATED_PER_RUNNER_PYTEST_LOG_SITES` /
    `_STATED_LITERAL_PYTEST_LOG_SITES`; migrating one more leg without updating them
    reds this test, and restoring the pre-fix split makes it fail on the pre-fix code.
    """
    per_runner = literal = 0
    for workflow in ATTRIBUTION_WORKFLOWS:
        for line in workflow.read_text().splitlines():
            target = capture_site(line)
            if target is None:
                continue
            # The `pytest.log`-named captures, and ONLY those: `pmv-pytest.log` and
            # the `pytest-d14.log` tee site are the other TWO sites in the inventory
            # (8 = these 6 + 2), named separately above the floor. `trackb.log` left the
            # inventory when #10280 moved that leg onto `"$PYTEST_LOG"` — this line
            # said "other three" for one head after that, which is the same
            # restated-inventory class the derived split above exists to stop.
            if target != _RUNNER_TEMP_LOG and not target.endswith("/pytest.log"):
                continue
            if target == _RUNNER_TEMP_LOG:
                # `"$PYTEST_LOG"` IS a `pytest.log` capture; its leaf name is not in
                # the token.
                per_runner += 1
                continue
            # ⛔ COMPARE THE PATH, NOT THE TOKEN (#7815, measured). The quoted
            # `${RUNNER_TEMP:-/tmp}/pytest.log` spelling ends with `/pytest.log"`, not
            # `/pytest.log`, so a raw-token suffix test dropped BOTH migrated legs out
            # of BOTH buckets: 5 pytest.log sites in the workflow, 3 counted, and the
            # guard reported a split that omitted the two legs it was measuring.
            token = target.strip('"')
            if _RUNNER_TEMP_TMPL_RE.match(target):
                if token.endswith("/pytest.log"):
                    per_runner += 1
                continue
            if token.startswith("/tmp/") and token.endswith("/pytest.log"):
                literal += 1

    assert per_runner == _STATED_PER_RUNNER_PYTEST_LOG_SITES, (
        f"the prose says {_STATED_PER_RUNNER_PYTEST_LOG_SITES} per-runner "
        f'`"$PYTEST_LOG"` legs, the workflows have {per_runner} — update BOTH the stated '
        "count and the comment block above the floor"
    )
    assert literal == _STATED_LITERAL_PYTEST_LOG_SITES, (
        f"the prose says {_STATED_LITERAL_PYTEST_LOG_SITES} `/tmp/pytest.log` literal "
        f"legs, the workflows have {literal} — update BOTH the stated count and the "
        "comment block above the floor"
    )
    # The six `pytest.log`-named tails the inventory enumerates. Asserting the total as
    # well means a migration that changes BOTH sides cannot pass by swapping them.
    assert per_runner + literal == 6, (
        f"{per_runner + literal} `pytest.log`-named tails, the inventory enumerates 6"
    )


def test_no_two_legs_capture_the_same_host_global_log() -> None:
    """A host-global log path may belong to ONE leg — two owners is not a red, it is NO VERDICT.

    This is the invariant the per-leg MIGRATION is for, stated directly instead of being
    reached through the migration's own bookkeeping (a count can be updated in step with a
    regression and stay green). Two jobs that capture the same `/tmp/*.log` on a host whose
    runner installs share one `/tmp` overwrite each other's file: the second writer TRUNCATES
    the first's header away, and every reader of that path — the `tail`, the reporter's
    session-ownership check, the `grep '^(FAILED|ERROR) '` that the admin rail derives a
    failure SET from, and the skip-guard's manifest — then describes a session the leg did
    not run.

    ⛔ MEASURED, and the reason this pin exists (run 40985248910 ATTEMPT 3, PR #10214).
    `test-concurrency-falkor` (runner `cmux`, attempt 3) read `/tmp/pytest.log` as a
    19-minute session from `test-carve-out`: the carve-out leg wrote over the
    concurrency leg's 3-minute window, and the concurrency job's log IS the carve-out
    session — `288 passed`, `test_smoke_embedded`, `test_eval_ingest_retry`, none of
    them its own tests, with `grep` reporting `binary file matches` over the NUL
    holes. Its reporter said `test leg INCOMPLETE — no pytest summary; failure set
    unobservable` (correct, fail-closed), and `ci-introduced-failure` then BLOCKED the
    PR on those three FOREIGN nodeids — a required check decided by a log its leg
    never wrote. WHICH leg wrote it is an inference from this file's inventory (a
    truncated log cannot name its other writer), which is why the pin below does not
    depend on identifying one: it derives ownership from the workflows.

    The migration has moved `test`, `test-slow`, `test-concurrency-falkor` and
    `test-carve-out` to the per-runner `"$PYTEST_LOG"`; the rest (`test-track-b`,
    `test-d14-hosted-api`, `validate`) is #10280. This pin is what makes the next
    leg's migration non-optional: a NEW job that reuses an existing host-global path
    reds here even if every stated count was updated to match.

    ⛔ TWO ARMS, because ONE ownership key is not one leg (PR #10214 review, P2). The
    dictionary below keys on the YAML JOB NAME, and a `strategy.matrix` job expands
    into N legs that run CONCURRENTLY — GitHub's own semantics, and the reason the
    self-hosted pool shows `test (b)`, `test (h)` and `test (c)` on different runner
    installs of ONE host in the same attempt. Every shard of a matrix is therefore the
    string `test-carve-out`, so `len(owners[target]) > 1` cannot see that leg collide
    with ITSELF: two shards writing one `/tmp/pytest.log` overwrite each other exactly
    as two jobs do, and the first arm would report nothing. The second arm reds a
    matrix job that captures a host-global path at all, and it is what carried this
    migration: `test-carve-out` was the one leg it fired on before the fix, and it
    goes quiet only because that leg now writes `$RUNNER_TEMP`.
    """
    owners: dict[str, set[str]] = {}
    matrix_sites: list[tuple[str, str]] = []
    for workflow in ATTRIBUTION_WORKFLOWS:
        matrix_jobs = _matrix_job_keys(workflow)
        for job, target in _capture_site_jobs(workflow):
            # A per-runner target is already per-job unique — only host-global paths can
            # collide, and a site attributed to no job cannot be reasoned about here.
            if job is None or not target.startswith("/tmp/"):
                continue
            owners.setdefault(target, set()).add(job)
            if job in matrix_jobs:
                matrix_sites.append((job, target))
    shared = {target: sorted(jobs) for target, jobs in owners.items() if len(jobs) > 1}
    assert shared == {}, (
        "two legs capture ONE host-global log path, so neither leg's file is its own — "
        "its tail, its attribution read and its failure set describe the co-tenant "
        f"(#10280): {shared}. Give the leg a per-runner path under $RUNNER_TEMP."
    )
    assert matrix_sites == [], (
        "a MATRIX job captures a host-global log path: `strategy.matrix` runs N legs "
        "CONCURRENTLY, so its shards are N owners of one file that the job-key check "
        "above reads as ONE — two shards on the same host truncate each other's log "
        "(#10280), and the failure set the admin rail derives from it belongs to a "
        f"sibling: {matrix_sites}. Give the leg a per-runner path under $RUNNER_TEMP."
    )


def _matrix_job_keys(workflow: Path) -> set[str]:
    """The job keys in ``workflow`` that declare ``strategy.matrix`` — i.e. N legs.

    Read from the parsed YAML, not from the text: the question is structural ("does
    this job expand?"), and a matrix spelled `${{ fromJSON(needs.changes.outputs...
    ) }}` still answers it — the KEY's presence is the fact, not its value.
    """
    jobs = yaml.safe_load(workflow.read_text()).get("jobs") or {}
    return {job for job, config in jobs.items()
            if isinstance(config, dict)
            and (config.get("strategy") or {}).get("matrix") is not None}


def _capture_site_jobs(workflow: Path) -> list[tuple[str | None, str]]:
    """``(job, capture target)`` for every log-capture site in ``workflow``.

    The job is resolved from the file's own structure (the 2-space-indented job keys
    under ``jobs:``), so a site added to a NEW job is attributed to that job without
    the test naming it. A site above the first job key resolves to ``None`` — no
    caller relies on that, it is only the honest answer for a stray capture.
    """
    text = workflow.read_text()
    lines = text.splitlines()
    job_starts: list[tuple[int, str]] = []
    for job in (yaml.safe_load(text).get("jobs") or {}):
        needle = f"  {job}:"
        for i, line in enumerate(lines):
            if line.rstrip() == needle:
                job_starts.append((i, job))
                break
    job_starts.sort()
    sites: list[tuple[str | None, str]] = []
    for i, line in enumerate(lines):
        target = capture_site(line)
        if target is None:
            continue
        owner = None
        for start, job in job_starts:
            if start <= i:
                owner = job
        sites.append((owner, target))
    return sites


def test_the_residual_header_is_scoped_to_the_legs_that_still_are_residuals() -> None:
    """The reporter's `NAMED RESIDUAL` header must describe the legs it still names.

    The header is the artifact the next `/tmp` migration lane reads — `python-ci.yml`'s
    own `test-slow` first step points a reader at it — and it sat SEVEN LINES above the
    `⚠️ STATUS: APPLIED TO BOTH MATRIX JOBS` paragraph that retires it (PR #10244
    review, P3). It said *"every leg writes the SAME global path"*, which stopped being
    true when #10028 migrated the fast matrix and the `test-slow` migration in this PR
    completed the second job, and #10280 then migrated the remaining `python-ci.yml`
    legs: every one of them now resolves its log under `${RUNNER_TEMP}`, so no leg of
    that workflow writes a host-global literal at all. (No count is stated here on
    purpose — the split has moved three times and a count in prose is not derived by
    any pin, so it goes stale silently. Read `_STATED_PER_RUNNER_PYTEST_LOG_SITES` /
    `_STATED_LITERAL_PYTEST_LOG_SITES` and
    `test_the_stated_inventory_matches_the_workflows` for the numbers.) A header
    claiming the retired premise teaches the opposite of what the code does, in the
    exact sentence the next lane quotes.

    ⛔ THE DELIVERED STATE (#7815, PR #10143): Four of the five legs this pin used to
    derive as residuals — `test-concurrency-falkor`, `test-track-b`, `test-carve-out`,
    `test-d14-hosted-api` — are MIGRATED, so a host-global capture target survives in
    exactly ONE leg, `post-merge-validation.yml`'s `validate` (`/tmp/pmv-pytest.log`).
    They are migrated in TWO spellings: #10280 moved the first three onto `"$PYTEST_LOG"`
    (resolved once per job from `${RUNNER_TEMP:?}`, fail-closed), while
    `test-d14-hosted-api` keeps this PR's per-step `${RUNNER_TEMP:-/tmp}/...`. Both are
    per-runner; neither writes a host-global `/tmp` artifact. That is a
    migration's COMPLETION of §10280's list, not a regression of it.

    ⛔ The set is EVERY host-global capture target, not the `/tmp/pytest.log` spelling
    (PR #10244 review, blocking): the first version of this pin filtered on that one
    literal, so it derived two legs and stayed green while `test-track-b`
    (`/tmp/trackb.log`), `test-d14-hosted-api` (`/tmp/pytest-d14.log`) and
    `post-merge-validation.yml`'s `validate` (`/tmp/pmv-pytest.log`) — all of them named
    on #10280 — were still un-migrated. A guard whose subject is preventing this drift
    was itself spelling-dependent, i.e. fail-open, and its own failure message told the
    next editor the two-element list was complete.

    The remaining legs are DERIVED from the workflows with `capture_site` (the same
    reading the floor uses) and asserted to be NAMED by the header — and that the header
    names no leg that has ALREADY migrated, since an over-wide residual sends the next
    lane to redo finished work. Equality, in both directions, on the statement rather
    than on a count. "Named" means named in the LIST block itself (bounded by
    `The legs that still write` … `THIS LIST IS DERIVED`), not somewhere in the header's
    prose: the paragraph below the list explains WHY a leg is in
    scope, and that explanation must not be able to stand in for a list entry.
    """
    header = SCRIPT.read_text()
    # MUTATION PROOF for the second half below: the pre-fix text stated the premise the
    # STATUS paragraph retires, so this assertion fails on the pre-fix file.
    assert "every leg writes the SAME global path" not in header, (
        "pytest-attribution.sh: the NAMED RESIDUAL header still claims EVERY leg writes "
        "the host-global path — #10028 migrated both matrix jobs, so the claim is false "
        "and contradicts the STATUS paragraph seven lines below it"
    )
    sites = [
        (job, target)
        for workflow in ATTRIBUTION_WORKFLOWS
        for job, target in _capture_site_jobs(workflow)
        if job is not None
    ]
    # ANY host-global `/tmp/*.log`, NOT just the `/tmp/pytest.log` spelling: filtering on
    # one literal is what let the four-leg residual read as a two-leg one.
    residual = sorted({job for job, target in sites if target.startswith("/tmp/")})
    migrated = {job for job, target in sites} - set(residual)
    # The delivered state, not a bound: a further migration changes this set and must
    # update the header's scope in the same change (that is the point of the pin).
    # `test-carve-out` left it when its MATRIX artifacts moved to `$RUNNER_TEMP`
    # (#10280's matrix half): a matrix job's shards are concurrent owners of one
    # host-global file, so it could not stay a residual of a `.log`-only migration.
    assert residual == [
        "validate",
    ], (
        "the legs still writing a host-global /tmp/*.log changed — update the "
        f"NAMED RESIDUAL scope in pytest-attribution.sh to match: {residual}"
    )
    # The header's residual LIST, bounded by its own delimiters: the `NAMED RESIDUAL`
    # paragraph sits ABOVE the STATUS paragraph and the migrated jobs' names live in
    # STATUS (`test`, `test-slow`), so a section spanning both would credit them as
    # residuals. Both delimiters are asserted present rather than `index()`ed blind — a
    # reflow that drops one must red here, not raise an unhelpful ValueError.
    #
    # ⛔ THE SECTION MUST END WHERE THE LIST ENDS, at the `THIS LIST IS DERIVED` block.
    # The commentary BELOW the list also names `test-track-b` and `validate` in prose
    # (why each is in scope), so a section that ran on to `So ownership is` let the LIST
    # lose an entry while this pin stayed green — measured, by deleting three list
    # entries: only `test-d14-hosted-api`, the one leg the prose does not repeat, was
    # reported missing. That is the same fail-open the widened `/tmp/*.log` derivation
    # above fixes, one layer in: the list is the map a migrating lane reads, and prose
    # that explains WHY a leg is listed is not a substitute for listing it.
    start_marker = "The legs that still write"
    end_marker = "THIS LIST IS DERIVED"
    assert start_marker in header and end_marker in header, (
        "pytest-attribution.sh: the NAMED RESIDUAL header lost its list delimiters "
        f"({start_marker!r} / {end_marker!r}) — the residual scope pin cannot read the "
        "statement it asserts"
    )
    section_start = header.index(start_marker)
    section = header[section_start : header.index(end_marker, section_start)]

    def _named(job: str) -> bool:
        # Boundaries that exclude `-` as well: `test` is a substring of `test-slow` and of
        # every hyphenated leg name, so `\b` alone would credit the wrong leg.
        return re.search(rf"(?<![\w-]){re.escape(job)}(?![\w-])", section) is not None

    unnamed = [job for job in residual if not _named(job)]
    assert not unnamed, (
        "pytest-attribution.sh: the NAMED RESIDUAL header does not name every leg that "
        f"still captures a host-global /tmp log — missing {unnamed} of {residual} (#10280)"
    )
    stale = sorted(job for job in migrated if _named(job))
    assert not stale, (
        "pytest-attribution.sh: the NAMED RESIDUAL header still names legs that HAVE "
        f"migrated to a per-runner path — drop {stale} from the residual scope (#10280)"
    )


def test_the_docstrings_inventory_split_is_derived_not_restated() -> None:
    """PR #10264 review P3 (F1): the split said "2 of them the per-runner" — one leg stale.

    The relocation that made `test-slow` write through `"$PYTEST_LOG"` is the SAME
    commit that made this paragraph wrong, and nothing noticed: the sentence and the
    closing floor comment in one docstring then stated two different inventories of
    one file. Deriving the split from `capture_site` (the counter the floor uses)
    means the prose cannot be true of an older head — an edit that changes either
    shape reds HERE, with the number to write.

    This pins the DOCSTRING's sentence; `test_the_stated_inventory_matches_the_workflows`
    pins the stated CONSTANTS. They are two different artifacts of the same number, and
    the defect this closes was in the prose — a constants-only pin would have stayed
    green through it.
    """
    sites = [s for s in (capture_site(ln) for ln in WORKFLOW.read_text().splitlines())
             if s is not None]
    per_runner = [s for s in sites if s == _RUNNER_TEMP_LOG]
    literal = [s for s in sites if s == "/tmp/pytest.log"]
    # the total this sentence states, and the split it got wrong
    assert len(per_runner) + len(literal) == 6, sites
    flat = " ".join(
        (test_every_pytest_log_tail_is_followed_by_the_reporter.__doc__ or "").split()
    )
    assert f"{len(per_runner)} of them the per-runner" in flat, (
        f"{WORKFLOW.name} has {len(per_runner)} per-runner and {len(literal)} literal "
        f"`/tmp/pytest.log` sites; the docstring's parenthetical must say so"
    )


def test_a_comment_mentioning_the_tee_shape_is_not_a_capture_site() -> None:
    """Prose is not a capture site — a documented spelling must not inflate the floor.

    The phantom is not hypothetical: the `tee` spelling is documentation in this PR's
    own body and in `tests/test_markers.py`, and it sits in the comment block directly
    above the D14 site — an entirely natural edit. An unanchored `re.search` counted
    every such comment, and because the assertion is a FLOOR, the phantom was
    invisible: it handed back exactly the one-site slack the floor raise removed.
    """
    comment = "          # this leg writes through | tee /tmp/pytest-d14.log"
    assert capture_site(comment) is None
    # MUTATION PROOF: the pre-fix unanchored matcher DID count that line. This is the
    # discrimination the test above depends on, asserted rather than asserted-about.
    assert re.search(r"\|\s*tee\s+(/tmp/[^\s|]+\.log)", comment.strip())
    # ...and the real shapes are still sites: filtering comments must not blind the
    # counter to the lines it exists to find.
    assert (
        capture_site(
            "            -v --timeout=300 2>&1 | tee /tmp/pytest-d14.log || rc=$?"
        )
        == "/tmp/pytest-d14.log"
    )
    # The ci.yml `hosted-e2e` leg is NOT a site: its workflow is outside
    # `ATTRIBUTION_WORKFLOWS` (deliberately — #9300), and a v1 of this counter that
    # scanned it would demand a reporter the leg does not have. Named here so the
    # deferral is a decision with a test, not an omission.
    assert (
        capture_site(
            "            -q -rs --capture=sys 2>&1 | tee /tmp/hosted-e2e.log || rc=$?"
        )
        == "/tmp/hosted-e2e.log"
    )
    assert capture_site("          tail -n 300 /tmp/pytest.log") == "/tmp/pytest.log"
    assert capture_site("              tail -n 200 /tmp/pytest.log") == "/tmp/pytest.log"
    # a NON-pytest log with a trailing command is not a site either (it would be a
    # phantom of a different kind: the counter would demand a reporter on a wrangler
    # log dump)
    assert capture_site("          tail -50 /tmp/wrangler-app.log || true") is None


# ── 4. the STEP's status: naming the test must not swallow the verdict ─────
#
# WHY EXECUTION HERE, where §3 reads text: what is pinned is a SHELL-SEMANTIC property
# — that a red pytest still reds the step once `|| rc=$?` has disarmed `set -e`. A text
# check on `[ "$rc" -eq 0 ] || exit "$rc"` would be satisfied by the line sitting in a
# comment or after an unreachable `exit`; #4494 is this repo's record of text/behaviour
# drift. So the step body is RUN under `bash -e` — the runner's default shell for an
# unspecified `shell:` — with a `python` stub that exits with the leg's own status.
#
# The status asserted is the LEG's (137), not merely "non-zero": deleting the re-raise
# does not make the D14 step pass by accident, it makes it exit 1 from the later
# `PASSED >= 30` floor, so a `!= 0` assertion would still be green on the mutation.

STEP_TIMEOUT_S = 120

#: `timeout [-s SIG] [-k DUR] DURATION CMD...` — drop timeout's own flags and its
#: DURATION positional, then exec CMD (macOS has no `timeout`). Same shim as
#: `tests/test_ci_guard_invocation.py`, which models these same steps by execution.
TIMEOUT_SHIM = """#!/bin/sh
while [ $# -gt 0 ]; do
  case "$1" in
    -s|-k) shift 2 ;;
    -*) shift ;;
    *) shift; break ;;
  esac
done
exec "$@"
"""


def _step_run_body(workflow: Path, job: str, step_name_fragment: str) -> str:
    """The `run:` block of the one step whose name carries `step_name_fragment`.

    A LOCATOR, never the assertion — and a FRAGMENT because a step name cannot carry
    `#`: in a YAML `name:`, `#` starts a comment after a space, so the D14 step's
    `(#4164)` is not part of its name at all (YAML parses it as
    `Run the embedded_only marker selection (URI unset —`). Matching the parsed name
    exactly would pin that accident instead of the step.
    """
    jobs = yaml.safe_load(workflow.read_text())["jobs"]
    matches = [
        step for step in jobs[job]["steps"] if step_name_fragment in (step.get("name") or "")
    ]
    assert len(matches) == 1, (
        f"{workflow.name}: expected exactly one step in {job!r} named like "
        f"{step_name_fragment!r}, found {len(matches)}"
    )
    body = matches[0].get("run")
    assert isinstance(body, str), f"{workflow.name}: {step_name_fragment!r} has no run block"
    return body


def _run_step_body(
    body: str, tmp_path: Path, pytest_rc: int
) -> subprocess.CompletedProcess[str]:
    """Run one step body as the runner does, with pytest exiting `pytest_rc`.

    `/tmp/...` paths are relocated into the sandbox so the run touches no shared file;
    `python`/`python3` are stubs that emit one pytest start line (so the `tee` writes a
    real log) and then exit with the leg's status. `bash -e` models the runner's
    default shell, and `cwd=REPO` is what makes `bash .github/scripts/...` resolve.
    """
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    script = tmp_path / "step.sh"
    script.write_text(body.replace("/tmp/", f"{sandbox}/"), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = (
        "#!/bin/sh\n"
        "echo 'tests/e2e/hosted/test_boot.py::test_serves_health '\n"
        f"exit {pytest_rc}\n"
    )
    for name in ("python", "python3"):
        exe = bin_dir / name
        exe.write_text(stub, encoding="utf-8")
        exe.chmod(0o755)
    shim = bin_dir / "timeout"
    shim.write_text(TIMEOUT_SHIM, encoding="utf-8")
    shim.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        # The step spells its log/junit as `${RUNNER_TEMP:-/tmp}/…` (#7815) and
        # the body rewrite below sandboxes `/tmp/`. Pin `RUNNER_TEMP` to the
        # EMPTY string so the `:-` fallback always wins: an ambient value would
        # aim the step at a real directory while the sandbox is what gets
        # rewritten, and the file the stub writes would never be the one read.
        "RUNNER_TEMP": "",
    }
    return subprocess.run(
        ["bash", "-e", str(script)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=STEP_TIMEOUT_S,
    )


@pytest.mark.parametrize(
    ("workflow", "job", "step", "log"),
    [
        (
            WORKFLOW,
            "test-d14-hosted-api",
            "Run the embedded_only marker selection",
            '"${RUNNER_TEMP:-/tmp}/pytest-d14.log"',
        ),
    ],
    ids=["d14"],
)
def test_a_failing_pytest_still_reds_the_step(
    workflow: Path, job: str, step: str, log: str, tmp_path: Path
) -> None:
    """A `| tee` leg that also REPORTS must still fail when its pytest failed.

    `rc=0` + `... | tee <log> || rc=$?` is what lets the reporter run after a kill —
    and it is also what disarms `set -e`, so the one-line re-raise is the ONLY thing
    left enforcing "a red suite reds the job". Delete or mangle it and the step
    reports SUCCESS on a failing suite while the reporter exits 0: a false PASS on a
    leg `python-ci-gate` requires (AGENTS.md §Product Over Process, category A).
    Nothing pinned it — the coverage window above matches the reporter call and says
    nothing about the status.

    The kill's own status (137) is asserted rather than "non-zero" on purpose: on the
    D14 step, dropping the re-raise lands on the later `PASSED >= 30` floor, which
    exits 1 — non-zero, and still a false PASS for the 15m watchdog case it is meant
    to report. Only the exact code separates "the leg's verdict" from "a later gate
    noticed an empty log".

    The shape exists at TWO sites — ci.yml's `hosted-e2e` leg has the same
    `| tee`-plus-reporter pipeline — but that leg is not wired yet (#9300), so its
    case is absent here rather than asserting a pipeline that does not exist.
    """
    body = _step_run_body(workflow, job, step)
    assert f"--log {log}" in body, f"{workflow.name}:{job}: no reporter call for {log}"
    result = _run_step_body(body, tmp_path, pytest_rc=137)
    assert result.returncode == 137, (
        "the step did not exit with the leg's own status — a red pytest was swallowed "
        f"by `|| rc=$?` and the re-raise is gone or unreachable:\nrc={result.returncode}"
        f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # ...and the reporter ran FIRST, naming what was in flight when the leg died.
    assert INCOMPLETE_MARKER in result.stdout, result.stdout
    assert "tests/e2e/hosted/test_boot.py::test_serves_health" in result.stdout, result.stdout


# ── 5. the kill banner states WHEN the leg died, not only how long its budget was ──
#
# #3719 (open) carries three measured instances of the SAME misreading, two of them
# on this PR's own required legs:
#
#   * #10917 `test (f)`, run 41843678044 — `rc=137` at **1215 s** of a **1620 s**
#     budget, reported as "killed after 27m".
#   * #10914 `test (a)`, run 41952788142 — `rc=137` at **346 s** of a **1560 s**
#     budget, with `##[error]The runner has received a shutdown signal` right after;
#     reported as "killed after 26m", and that sentence is the line the dispatch
#     brief for this very lane quoted as its reason to exist.
#   * #10914 `test-carve-out (b)`, same run — a COMPLETE `505 passed in 518.25s`,
#     step still `rc=137`, reported as "killed after 19m".
#
# `timeout`'s own manual is why the exit code cannot answer it: 124 means "the
# deadline was reached", 137 is 128+SIGKILL and is produced BOTH by `timeout -k`'s
# follow-up kill AND by an unrelated OOM or a runner shutdown. The ELAPSED time
# separates them — a watchdog kill cannot land before the deadline it implements —
# and the step already has it (`$SECONDS` stamped just before the `timeout` call).
#
# Two layers, matching the ones above: the script is EXECUTED against the measured
# instances (so the sentence is tested, not the string), and the workflow is checked
# STRUCTURALLY for the call + its start stamp at every banner site (so a newly added
# leg cannot quietly restore the bare budget-as-elapsed sentence).

KILL_SCRIPT = REPO / ".github" / "scripts" / "pytest-kill-attribution.sh"

#: (rc, elapsed, budget_minutes, the arm the measured instance belongs to)
#: Each elapsed/budget pair is a MEASURED run, not an invented one — see above.
MEASURED_KILLS = [
    (137, 1215, 27, "outside"),   # #10917 test (f)
    (137, 346, 26, "outside"),    # #10914 test (a)   — runner shutdown
    (137, 518, 19, "outside"),    # #10914 test-carve-out (b) — after a full summary
    (124, 1561, 26, "deadline"),  # a real watchdog expiry (SIGINT at the deadline)
    (137, 1562, 26, "deadline"),  # timeout's -k 10 follow-up on an ignored SIGINT
    (2, 1560, 26, "deadline"),    # pytest's own SIGINT summary at the deadline
]


def run_kill_attribution(
    *argv: str, timeout: float = 60
) -> subprocess.CompletedProcess[str]:
    """Run the attribution reporter the way the workflow does."""
    return subprocess.run(
        ["bash", str(KILL_SCRIPT), *argv],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


@pytest.mark.parametrize(
    ("rc", "elapsed", "budget_min", "arm"),
    MEASURED_KILLS,
    ids=[f"{rc}-{elapsed}s-of-{b}m-{arm}" for rc, elapsed, b, arm in MEASURED_KILLS],
)
def test_a_measured_kill_is_attributed_to_the_right_cause(
    rc: int, elapsed: int, budget_min: int, arm: str
) -> None:
    """The measured instances are classified by ELAPSED vs BUDGET, not by rc.

    This is the whole point: `rc` alone cannot separate them (124/137/2 all reach
    this banner through different histories), so the test asserts the two arms
    disagree on the SAME rc and agree across different ones.
    """
    result = run_kill_attribution(
        "--rc", str(rc), "--elapsed", str(elapsed), "--budget-min", str(budget_min)
    )
    assert result.returncode == 0, result.stderr
    budget_s = budget_min * 60
    if arm == "outside":
        assert "BEFORE the watchdog could fire" in result.stdout, result.stdout
        assert f"{budget_s - elapsed}s BEFORE" in result.stdout, result.stdout
        assert "is NOT the cause" in result.stdout, result.stdout
        # The actionable sentence: raising the budget is the WRONG lever here.
        assert "Do not raise watchdog_minutes" in result.stdout, result.stdout
    else:
        assert "the watchdog's own deadline was reached" in result.stdout, result.stdout
        assert "is the lever" in result.stdout, result.stdout
        assert "BEFORE the watchdog could fire" not in result.stdout, result.stdout


def test_rc_137_before_the_budget_is_never_read_as_a_budget_exhaustion() -> None:
    """The regression that cost two triage cycles: 137 at 346 s of 1560 s.

    Asserted separately from the parametrized table because this is the exact
    shape that reddened BOTH of this PR's failing required legs, and because a
    single-line edit to the comparison (`-lt` → `-ge`) would still satisfy the
    table's `outside` arm on one row while inverting this one.
    """
    result = run_kill_attribution("--rc", "137", "--elapsed", "346", "--budget-min", "26")
    assert result.returncode == 0, result.stderr
    assert "died 346s into a 1560s budget" in result.stdout, result.stdout
    assert "1214s BEFORE" in result.stdout, result.stdout
    assert "runner shutdown/cancel" in result.stdout, result.stdout
    assert "#3719" in result.stdout, result.stdout


def test_a_missing_start_stamp_fails_closed() -> None:
    """No elapsed must NOT default to 0 — that would assert an external kill on nothing.

    The workflow passes `--elapsed "${PYTEST_T0:+…}"`, which expands to the EMPTY
    string when the stamp is missing (a step body edited to drop it, a shell that
    resets `SECONDS`). `0` would land squarely in the "outside the budget" arm and
    print a confident wrong cause; UNKNOWN is the honest report.
    """
    for absent in (["--elapsed", ""], []):
        result = run_kill_attribution("--rc", "137", "--budget-min", "26", *absent)
        assert result.returncode == 0, result.stderr
        assert "elapsed UNKNOWN" in result.stdout, (absent, result.stdout)
        assert "BEFORE the watchdog could fire" not in result.stdout, result.stdout
        assert "deadline was reached" not in result.stdout, result.stdout
    # ...and a non-numeric stamp is the same unknown, not a crash.
    junk = run_kill_attribution("--rc", "137", "--elapsed", "12s", "--budget-min", "26")
    assert junk.returncode == 0, junk.stderr
    assert "elapsed UNKNOWN" in junk.stdout, junk.stdout


def test_a_leg_that_exited_normally_gets_no_attribution() -> None:
    """Only a kill owes a sentence: rc=0/1 must print nothing at all.

    The banner's own guard is `rc ∈ {124,137,2}`, and a second opinion printed on a
    normal failure would put a budget-shaped sentence on a leg whose nodeids are the
    answer — the same false attribution, in the other direction.
    """
    for rc in (0, 1, 5):
        result = run_kill_attribution("--rc", str(rc), "--elapsed", "10", "--budget-min", "26")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "", result.stdout


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--rc", "137"],
        ["--budget-min", "26"],
        ["--rc", "abc", "--elapsed", "1", "--budget-min", "26"],
        ["--rc", "137", "--elapsed", "1", "--budget-min", "twenty-six"],
        ["--rc", "137", "--elapsed", "1", "--budget-min", "26", "--nope"],
    ],
    ids=["no-args", "no-budget", "no-rc", "junk-rc", "junk-budget", "unknown-flag"],
)
def test_kill_attribution_bad_invocation_is_exit_2(argv: list[str]) -> None:
    """A misuse of the KILL reporter is a usage error, so it cannot read as a report.

    Named distinctly from `test_bad_invocation_is_exit_2` above (which covers the
    older `pytest-attribution.sh`): two same-named definitions in one module are a
    silent shadow — the earlier one stops being collected and `ruff`'s F811 reds the
    required `lint` leg, which is how this pair was caught.
    """
    assert run_kill_attribution(*argv).returncode == 2, argv


@pytest.mark.parametrize(
    "argv",
    [["--rc"], ["--budget-min"], ["--elapsed"], ["--rc", "137", "--elapsed"]],
    ids=["rc-last", "budget-last", "elapsed-last", "elapsed-last-after-a-pair"],
)
def test_a_flag_with_a_missing_value_is_exit_2_not_a_hang(argv: list[str]) -> None:
    """A trailing flag must be a usage error, not a spin loop.

    Measured at `dbf7cf9d8` (the head this was found on): `timeout 3 bash
    .github/scripts/pytest-kill-attribution.sh --rc 137 --elapsed` exits **124**
    — killed by `timeout` because it never terminated. Each arm read its value
    with `${2:-}` and then ran `shift 2` unconditionally; when the flag is the
    LAST argv element `shift 2` fails (`2 > $#`), shifts nothing, and — with
    `set -uo pipefail` and no `-e` — the `while [ $# -gt 0 ]` loop re-reads the
    same flag for ever. The banner then looks like a wedged shell rather than
    the usage error it is, and the reporter's stated purpose is that a human or
    a lane reruns it BY HAND.

    The bound below is what turns the pre-fix hang into a FAILURE instead of a
    60 s stall per case; the guard is on the count of remaining argv, so
    `["--elapsed", ""]` (the documented absent-stamp call) must still report
    UNKNOWN — see `test_a_missing_start_stamp_fails_closed` for that direction.
    """
    try:
        result = run_kill_attribution(*argv, timeout=10)
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"{argv} HUNG: a flag with a missing value must be a usage error "
            "(exit 2), not a spin loop. `shift 2` on a one-element tail shifts "
            "nothing and `set -uo pipefail` carries no `-e`, so the argv loop "
            "never advances — guard each arm on `[ $# -ge 2 ]` before shifting."
        )
    assert result.returncode == 2, (argv, result.returncode, result.stdout, result.stderr)
    # The message names the flag that was left dangling — the LAST argv element,
    # which is `--elapsed` in the shape where an earlier pair is already consumed.
    assert f"{argv[-1]} requires a value" in result.stderr, (argv, result.stderr)


def test_the_reporter_is_never_the_verdict() -> None:
    """Every REPORTING arm exits 0, so the step's status stays pytest's own.

    `_run_step_body`'s "a failing pytest still reds the step" case above is the
    other half of this contract: the reporter runs before `exit $rc`, and a non-zero
    reporter would mask the leg's real status.
    """
    for argv in (
        ["--rc", "137", "--elapsed", "346", "--budget-min", "26"],
        ["--rc", "124", "--elapsed", "1561", "--budget-min", "26"],
        ["--rc", "137", "--budget-min", "26"],
        ["--rc", "0", "--elapsed", "1", "--budget-min", "26"],
    ):
        assert run_kill_attribution(*argv).returncode == 0, argv


#: `timeout -s INT -k 10 <budget>m stdbuf -oL python -m pytest …` — the ONE line per
#: leg whose expiry the banner is about. Matched on the `pytest` invocation so a
#: comment that merely quotes the shape cannot be counted as a site.
_TIMEOUT_PYTEST_RE = re.compile(r"^timeout\s+.*\bpython\s+-m\s+pytest\b")
_START_STAMP = "PYTEST_T0=$SECONDS"


def _kill_banner_sites(lines: list[str]) -> list[int]:
    """Indices of the lines that print `WATCHDOG: … killed after <budget>m`."""
    return [
        i
        for i, line in enumerate(lines)
        if "WATCHDOG:" in line and "killed after" in line and not line.strip().startswith("#")
    ]


def test_every_kill_banner_is_followed_by_the_measured_attribution() -> None:
    """EVERY `killed after <budget>m` banner is followed by the measured sentence.

    The defect is per-leg, so a newly added leg that prints the banner without the
    attribution silently restores the false sentence at that site — which is exactly
    how #10914 lost two triage cycles. The assertion is a FLOOR over the three sites
    `python-ci.yml` actually has — the 40m `test-slow` leg included, whose own header
    cites the 39.6-min-against-40m instance — plus the pairing each site must carry:

    * a `PYTEST_T0=$SECONDS` stamp BETWEEN that leg's `timeout … python -m pytest`
      line and its banner (a stamp placed at step start, or dropped, either counts
      the last-failed pre-phase as pytest time or reports nothing); and
    * the reporter call in the banner's own arm, so the numbers handed to it are
      this leg's.

    ⛔ NAMED OMISSION, not a silent gap: `post-merge-validation.yml` has the same
    banner (`WATCHDOG: pytest killed after 50m`) with a LITERAL budget and no start
    stamp. It is a post-merge leg, not one of the required PR contexts this lane
    reddened, so it is deliberately out of scope here — wiring it is the same
    three-line change at its site (recorded on #3719).
    """
    lines = WORKFLOW.read_text().splitlines()
    sites = _kill_banner_sites(lines)
    assert len(sites) == 3, (
        "the kill-banner inventory moved — re-derive it from the workflow and update "
        f"this floor deliberately, found {len(sites)} site(s) at "
        + ", ".join(str(i + 1) for i in sites)
    )
    for site in sites:
        # The report is emitted as prose, so the call is separated from the banner by
        # the rationale comment block. Walk the non-comment lines instead of a fixed
        # line window: a window that is too narrow silently checks nothing, and one
        # that is too wide could pair a site with its NEIGHBOUR's call.
        following = [
            ln for ln in lines[site + 1 : site + 25] if not ln.strip().startswith("#")
        ]
        assert following, f"python-ci.yml:{site + 1}: nothing follows the kill banner"
        first = next(
            (i for i, ln in enumerate(following) if "pytest-kill-attribution.sh" in ln),
            None,
        )
        assert first is not None, (
            f"python-ci.yml:{site + 1}: the kill banner prints the BUDGET as if it were "
            "the elapsed time and nothing follows it — a triager reads 'killed after "
            "Nm' as the cause (#3719, #10914)"
        )
        # The call is a `\`-continued bash command; its own lines carry the flags.
        call = [following[first]]
        for ln in following[first + 1 :]:
            if not call[-1].rstrip().endswith("\\"):
                break
            call.append(ln)
        arm = "\n".join(call)
        assert "--elapsed" in arm, (
            f"python-ci.yml:{site + 1}: the call passes no elapsed:\n{arm}"
        )
        assert "PYTEST_T0" in arm, (
            f"python-ci.yml:{site + 1}: the elapsed is not derived from a pytest start "
            f"stamp — a step-start `$SECONDS` counts the last-failed pre-phase:\n{arm}"
        )
        assert "--budget-min" in arm, (
            f"python-ci.yml:{site + 1}: the call passes no budget:\n{arm}"
        )
        # ...and the stamp sits between THIS leg's pytest invocation and its banner.
        # `timeout -s INT -k 10 …` and the stamp are adjacent, so the search starts at
        # the PREVIOUS leg's invocation (or the top of the file) — a stamp counted from
        # the top could be another leg's, and one placed at step start would count the
        # last-failed pre-phase as pytest time.
        timeouts = [
            i for i in range(site) if _TIMEOUT_PYTEST_RE.match(lines[i].strip())
        ]
        assert timeouts, (
            f"python-ci.yml:{site + 1}: no `timeout … python -m pytest` line above this "
            "banner — the site moved and this guard no longer knows what it is checking"
        )
        timeout_at = timeouts[-1]
        start_at = timeouts[-2] + 1 if len(timeouts) > 1 else 0
        stamps = [
            i for i in range(start_at, site) if lines[i].strip() == _START_STAMP
        ]
        assert stamps, (
            f"python-ci.yml:{timeout_at + 1}: pytest's start is not stamped before the "
            f"`timeout` call that the banner at line {site + 1} reports on"
        )
