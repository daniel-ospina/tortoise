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

    Seven sites today: 4 `pytest.log` legs, 1 `pmv-pytest.log`, and 1
    `trackb.log` (the must-pass Track B member, whose log name does not contain
    "pytest" — it was the coverage gap a first cut missed). The filter is
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
            if not m:
                continue
            target = m.group(1)
            if not (target.startswith("/tmp/") and target.endswith(".log")):
                continue
            tails += 1
            window = "\n".join(lines[i + 1 : i + 14])
            assert f"pytest-attribution.sh --log {target}" in window, (
                f"{workflow.name}:{i + 1}: the `{target}` tail is not followed by the "
                "reporter — this leg can silently omit its own failure set (#3470)"
            )
    # 4 `pytest.log` legs + `pmv-pytest.log` + `trackb.log`. A drop here means a
    # leg was renamed out from under the check, not that it got safer.
    assert tails >= 6, f"only {tails} pytest-log tails found — this check has rotted"
