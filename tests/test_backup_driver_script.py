"""#2823 driver-script contract tests for `.github/scripts/registry-cron.sh`.

The hourly DR driver is the only unattended consumer of the sweep result, and
three of its behaviours are load-bearing for #2823:

1. **`WATCHER_DOWN` closes only on watcher evidence.** A sweep that backed up
   proves the app answered and R2 round-tripped; it proves nothing about the
   staleness daemon (a separate thread the sweep never touches). On 2026-09-10
   the driver filed `WATCHER_DOWN` and closed it 8 seconds later as "a healthy
   run" while the watcher's heartbeat had been stale for 30 days (since the
   #669 flip). The close is now keyed on the fresh heartbeat measured in step 2
   (`WATCHER_HEARTBEAT_OK`), whatever the sweep status; `APP_DOWN`/`R2_DOWN`
   still close on a completed round trip (`backed_up`/`degraded`/no-op).

2. **The resolved control-plane dialect is logged.** The sweep result carries
   `source` (`supabase`|`registry`); the driver must render it, because a
   wrong-dialect read is otherwise identical to an empty deployment in every
   artefact we keep — the reason this defect looked healthy for 31 days.

3. **An untrusted sweep is loud.** The sweep reports `enum_failed` (never a
   benign `no_teams`) for a control-plane dialect mismatch; a sweep CALL that
   produced no credible status (`error` — empty body / 5xx / malformed, e.g.
   the control plane could not be resolved and the handler 500'd) is the same
   class of "no backups ran"; and a 0-backup result whose own numbers or
   dialect contradict it (`graph_totals.errors > 0` — the shape a
   never-backing-up sweep takes when every graph errors, since there is no
   `backed_up` to degrade — or no recognizable `source`, the #2823 shape during
   a version-skew window) is the same class again. All must fail the run red,
   because the GitHub/Telegram alert layer is a separate, currently-deaf path
   (#2828).

Every behaviour above is asserted by **executing the real script text** in bash
with stubbed I/O — never by grepping the source. A prior revision of this file
asserted behaviours 2 and 3 with substring checks; a reviewer probe wrapping the
red-exit gate in `if false; then … fi` (making the driver unable to ever go red)
left those greps green. Each harness slices the live region between **code**
anchors (never prose comments, so a comment reword cannot fail the suite) and
raises loudly if an anchor moves.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_SCRIPT = (Path(__file__).resolve().parent.parent
           / ".github" / "scripts" / "registry-cron.sh")

# Code anchors (statements, not section comments) — a pure comment/renumber edit
# must not break these tests, while a contract move must.
_WATCHER_STEP_START = 'WATCHER_RUNNING="$(printf'
_SWEEP_STEP_START = "SWEEP_FAILED=0"
_SWEEP_STEP_END = "PURGE_FAILED=0"
_SELFHEAL_START = 'if [ -n "$SWEEP_UNTRUSTED" ]; then'
_GATE_START = 'if [ "$SWEEP_FAILED" = "1" ]; then'

# The script bodies the driver must interpret (real sweep result shapes).
_NO_TEAMS_BODY = '{"status": "no_teams", "teams_backed_up": 0, "source": "supabase"}'
_NO_WORK_BODY = '{"status": "no_work", "teams_backed_up": 0, "source": "supabase"}'
_ALREADY_RUNNING_BODY = ('{"status": "already_running", "teams_backed_up": 0, '
                         '"source": "registry"}')
_NO_ELIGIBLE_TEAMS_BODY = ('{"status": "no_eligible_teams", "teams_backed_up": 0, '
                           '"source": "supabase"}')
_BACKED_UP_BODY = ('{"status": "backed_up", "teams_backed_up": 2, '
                   '"source": "supabase"}')
_DEGRADED_BODY = ('{"status": "degraded", "teams_backed_up": 1, '
                  '"source": "registry"}')
_ENUM_FAILED_BODY = ('{"status": "enum_failed", "teams_backed_up": 0, '
                     '"source": "registry", '
                     '"error": "team enumeration failed: control-plane dialect '
                     'mismatch"}')
# #2823 corroboration shapes: a 0-backup result the numbers/dialect contradict.
_NO_WORK_WITH_ERRORS_BODY = ('{"status": "no_work", "teams_backed_up": 0, '
                             '"source": "supabase", '
                             '"graph_totals": {"attempted": 1, "backed_up": 0, '
                             '"errors": 1}}')
_NO_TEAMS_NO_SOURCE_BODY = '{"status": "no_teams", "teams_backed_up": 0}'
_NO_WORK_NO_SOURCE_BODY = '{"status": "no_work", "teams_backed_up": 0}'
_NO_TEAMS_EMPTY_BODY = ('{"status": "no_teams", "teams_backed_up": 0, '
                        '"source": "supabase", '
                        '"graph_totals": {"attempted": 0, "backed_up": 0, '
                        '"errors": 0}}')
_MALFORMED_BODY = "502 Bad Gateway"
_HTTP_500_BODY = '{"detail": "Internal Server Error"}'


def _text() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _slice(start_marker: str, end_marker: str | None, *,
           last: bool = False) -> str:
    """Slice a live region of the driver between real CODE anchors.

    ValueError (anchor absent) fails the test loudly — a silently-empty slice
    would make every assertion over it vacuous.

    ``last=True`` anchors on the LAST occurrence: the step-6 self-heal guard
    (`if [ -n "$SWEEP_UNTRUSTED" ]; then`) is byte-identical to the one closing
    the step-3 latch, and slicing from the first would execute two regions that
    are not adjacent in the real script.
    """
    text = _text()
    start = text.rindex(start_marker) if last else text.index(start_marker)
    end = len(text) if end_marker is None else text.index(end_marker, start)
    return text[start:end]


def _run_bash(lines: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", "\n".join(lines)], capture_output=True,
                          text=True, timeout=timeout)


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _preamble() -> list[str]:
    """Stubs + variables every harness needs. `log`/`fail` mirror the real
    shapes (`fail` echoes to stderr and does NOT exit — the `exit 1` after it is
    what must make the run red, which is exactly what the exit code asserts)."""
    return [
        "set -euo pipefail",
        "API=http://driver.test",
        "KEY=k",
        "log() { echo \"[backup-driver] $*\"; }",
        "fail() { echo \"[backup-driver] ERROR: $*\" >&2; }",
        "gh_find_open() { echo \"OPEN_$1\"; }",
        "gh_close() { echo \"CLOSE $1\"; }",
    ]


def _run_selfheal(run_status: str, watcher_ok: str = "1",
                  untrusted: str = "") -> str:
    """Execute the real self-heal block with stubbed alert I/O.

    ``watcher_ok`` is step 2's `WATCHER_HEARTBEAT_OK` — the ONLY watcher
    evidence the driver collects; the WATCHER_DOWN close must follow it, not the
    sweep status. ``untrusted`` is step 3's latch verdict (`SWEEP_UNTRUSTED`);
    a non-empty value means the run heals nothing at all (#2823)."""
    proc = _run_bash([
        *_preamble(),
        f'RUN_STATUS="{run_status}"',
        f'WATCHER_HEARTBEAT_OK="{watcher_ok}"',
        f'SWEEP_UNTRUSTED="{untrusted}"',
        _slice(_SELFHEAL_START, _GATE_START, last=True),
    ])
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _run_sweep_step(body: str, curl_log: Path | None = None) -> tuple[str, str]:
    """Execute the REAL sweep step (curl → jq → log → untrusted-status gate)
    with a stubbed `curl`. Returns (stdout, SWEEP_FAILED).

    ``curl_log`` captures the stub's arguments so the REQUEST contract (the
    driver POSTs the sweep endpoint) can be asserted too, not just the response
    handling."""
    preamble = [*_preamble()]
    if curl_log is not None:
        preamble.append(
            f"curl() {{ printf '%s' \"$*\" > {_shell_quote(str(curl_log))}; "
            f"printf '%s' {_shell_quote(body)}; }}")
    else:
        preamble.append(f"curl() {{ printf '%s' {_shell_quote(body)}; }}")
    proc = _run_bash([
        *preamble,
        _slice(_SWEEP_STEP_START, _SWEEP_STEP_END),
        'echo "SWEEP_FAILED=$SWEEP_FAILED"',
    ])
    assert proc.returncode == 0, proc.stderr
    failed = proc.stdout.rsplit("SWEEP_FAILED=", 1)[1].strip()
    return proc.stdout, failed


def _run_chain(body: str, *, watcher_json: str | None = None) -> subprocess.CompletedProcess:
    """Execute the driver's real DECISION CHAIN in one shell: watcher
    supervision (step 2, when ``watcher_json`` is given) → sweep step (parse
    status/source, latch credibility) → self-heal (consumes RUN_STATUS and the
    step-2 heartbeat verdict) → failure gates (consume SWEEP_FAILED). This is
    the sequencing the journey depends on — the `/status` watcher reading, the
    sweep response, the heal set and the exit code all driving each other in
    one real execution — rather than four literals pinned independently."""
    lines = [*_preamble(), 'file_alert() { echo "FILE $1"; }']
    if watcher_json is not None:
        lines.append(f"STATUS={_shell_quote(watcher_json)}")
        lines.append(_slice(_WATCHER_STEP_START, _SWEEP_STEP_START))
    else:
        lines.append("WATCHER_HEARTBEAT_OK=1")
    lines += [
        f"curl() {{ printf '%s' {_shell_quote(body)}; }}",
        "PURGE_FAILED=0",
        "RECONCILE_FAILED=0",
        _slice(_SWEEP_STEP_START, _SWEEP_STEP_END),   # sets RUN_*, SWEEP_FAILED
        _slice(_SELFHEAL_START, _GATE_START,          # consumes RUN_STATUS
               last=True),
        _slice(_GATE_START, None),                     # consumes SWEEP_FAILED
    ]
    return _run_bash(lines)


def test_chain_files_and_keeps_watcher_down_when_the_heartbeat_is_stale():
    """The whole #2823 watcher story in one execution: a stale heartbeat at
    step 2 files WATCHER_DOWN, and the productive sweep that follows does NOT
    close it — pre-fix, this exact run filed and closed it 8 seconds apart."""
    stale = '{"watcher": {"running": true, "age_minutes": 99}}'
    proc = _run_chain(_BACKED_UP_BODY, watcher_json=stale)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "FILE WATCHER_DOWN" in proc.stdout, proc.stdout
    assert "CLOSE OPEN_APP_DOWN" in proc.stdout, proc.stdout
    assert "CLOSE OPEN_WATCHER_DOWN" not in proc.stdout, proc.stdout


def test_chain_closes_watcher_down_on_a_fresh_heartbeat_noop_run():
    """The complement: a fresh heartbeat lets a 0-team no-op close the stale
    WATCHER_DOWN that a previous run filed."""
    fresh = '{"watcher": {"running": true, "age_minutes": 1}}'
    proc = _run_chain(_NO_TEAMS_BODY, watcher_json=fresh)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "FILE WATCHER_DOWN" not in proc.stdout, proc.stdout
    for kind in ("APP_DOWN", "WATCHER_DOWN", "R2_DOWN"):
        assert f"CLOSE OPEN_{kind}" in proc.stdout, (kind, proc.stdout)


def test_chain_reports_a_dead_watcher_daemon_as_stale():
    """`watcher.running: false` (the daemon thread is not alive) is stale even
    with a low age, and must never be closed by the sweep that follows."""
    dead = '{"watcher": {"running": false, "age_minutes": 1}}'
    proc = _run_chain(_BACKED_UP_BODY, watcher_json=dead)
    assert "FILE WATCHER_DOWN" in proc.stdout, proc.stdout
    assert "CLOSE OPEN_WATCHER_DOWN" not in proc.stdout, proc.stdout


def _run_gate(sweep_failed: str) -> subprocess.CompletedProcess:
    """Execute the REAL failure-gate tail (from the SWEEP_FAILED gate to EOF)."""
    return _run_bash([
        *_preamble(),
        f'SWEEP_FAILED="{sweep_failed}"',
        "RUN_STATUS=enum_failed",
        "PURGE_FAILED=0",
        "RECONCILE_FAILED=0",
        _slice(_GATE_START, None),
    ])


@pytest.mark.parametrize("run_status", ["backed_up", "degraded"])
def test_selfheal_closes_every_kind_on_a_fresh_heartbeat(run_status):
    """A run that backed something up (or degraded with >=1 default backed up,
    #2411) with a FRESH watcher heartbeat is evidence the whole stack is up."""
    out = _run_selfheal(run_status, watcher_ok="1")
    for kind in ("APP_DOWN", "WATCHER_DOWN", "R2_DOWN"):
        assert f"CLOSE OPEN_{kind}" in out, (run_status, kind, out)


@pytest.mark.parametrize("run_status", ["backed_up", "degraded", "no_teams",
                                        "no_eligible_teams", "no_work"])
def test_stale_heartbeat_never_closes_watcher_down(run_status):
    """#2823: the load-bearing regression. A STALE heartbeat leaves WATCHER_DOWN
    open for EVERY status — including a productive `backed_up` run (the watcher
    is a separate thread the sweep never touches, so a successful backup says
    nothing about it). The pre-fix driver closed it for `backed_up`/`degraded`
    in the same invocation that filed it, which is how a 30-day-stale watcher
    read healthy.

    Asserted on the STUB invocations (`CLOSE OPEN_<kind>`), not on the raw text:
    the driver's own diagnostic line prints the heal KINDS, so a behaviour-
    preserving reword of that log must not turn this test red."""
    out = _run_selfheal(run_status, watcher_ok="0")
    assert "CLOSE OPEN_APP_DOWN" in out
    assert "CLOSE OPEN_R2_DOWN" in out
    assert "CLOSE OPEN_WATCHER_DOWN" not in out, (run_status, out)


@pytest.mark.parametrize("run_status", ["no_teams", "no_eligible_teams", "no_work"])
def test_noop_run_closes_watcher_down_only_on_a_fresh_heartbeat(run_status):
    """A 0-backup no-op proves the app answered and R2 round-tripped (its
    roll-up was written) — so it heals those two — and it heals WATCHER_DOWN
    when, and only when, step 2 measured a fresh heartbeat."""
    fresh = _run_selfheal(run_status, watcher_ok="1")
    assert "CLOSE OPEN_WATCHER_DOWN" in fresh, (run_status, fresh)
    stale = _run_selfheal(run_status, watcher_ok="0")
    assert "CLOSE OPEN_WATCHER_DOWN" not in stale, (run_status, stale)


def test_unhealthy_run_does_not_selfheal():
    """The self-heal block stays gated on the completed-round-trip statuses."""
    for status in ("enum_failed", "error", "already_running"):
        out = _run_selfheal(status)
        assert "CLOSE" not in out, status


@pytest.mark.parametrize("run_status", ["no_work", "no_teams", "backed_up"])
def test_untrusted_run_heals_nothing(run_status):
    """A result step 3 latched as untrusted must not close incidents on its way
    to going red — otherwise a sweep that backed nothing up (and whose numbers
    contradicted it) would still look like health to the incident ledger."""
    out = _run_selfheal(run_status, watcher_ok="1",
                        untrusted="no_work with graph_totals.errors=1")
    assert "CLOSE" not in out, (run_status, out)
    assert "self-heal skipped" in out, out


@pytest.mark.parametrize("body,expected", [
    (_NO_TEAMS_BODY, "supabase"),
    (_BACKED_UP_BODY, "supabase"),
    (_ENUM_FAILED_BODY, "registry"),
    # A body with no `source` (older app build, malformed 5xx) degrades to the
    # literal `unknown` rather than swallowing the line.
    (_MALFORMED_BODY, "unknown"),
    ('{"status": "backed_up", "teams_backed_up": 2}', "unknown"),
])
def test_sweep_step_logs_the_resolved_source(body, expected):
    """#2823: the driver renders the dialect the sweep actually enumerated, in
    the driver's own output — asserted from EXECUTED stdout, not the source."""
    out, _ = _run_sweep_step(body)
    assert f"[backup-driver] sweep source: {expected}" in out, out


@pytest.mark.parametrize("body,expected_failed", [
    # Benign outcomes — the run stays green.
    (_BACKED_UP_BODY, "0"),
    (_DEGRADED_BODY, "0"),
    (_NO_TEAMS_BODY, "0"),
    (_NO_WORK_BODY, "0"),
    (_ALREADY_RUNNING_BODY, "0"),
    # Every member of the driver's benign set is pinned: dropping one silently
    # turns ordinary production runs (e.g. a 0-eligible-team team sweep) red
    # every hour with no test signal.
    (_NO_ELIGIBLE_TEAMS_BODY, "0"),
    # A corroborated empty deployment: 0 backups, 0 errors, known dialect.
    (_NO_TEAMS_EMPTY_BODY, "0"),
    # Untrusted — the run goes red.
    (_ENUM_FAILED_BODY, "1"),
    (_HTTP_500_BODY, "1"),
    (_MALFORMED_BODY, "1"),
    ("", "1"),
    # A 0-backup result the numbers contradict: teams were enumerated and every
    # graph errored (`no_work` cannot read `degraded` without a `backed_up`), so
    # the run backed nothing up and must not read green.
    (_NO_WORK_WITH_ERRORS_BODY, "1"),
    # ...or the dialect contradicts it: no recognizable `source` is the #2823
    # shape itself (older app build / a caller bypassing the shared seam).
    (_NO_TEAMS_NO_SOURCE_BODY, "1"),
    (_NO_WORK_NO_SOURCE_BODY, "1"),
])
def test_sweep_step_latches_only_untrusted_results_as_failed(body, expected_failed):
    """#2823: every status the driver does not recognise as benign is untrusted
    and latches the run red — a fresh status defaults LOUD, not silently green.
    A refused enumeration and a sweep CALL that never returned a credible
    status (5xx / empty body) both mean no backup ran."""
    out, failed = _run_sweep_step(body)
    assert failed == expected_failed, out


def test_gate_fails_red_when_enumeration_is_refused():
    """#2823: an untrusted sweep makes the run RED (non-zero exit). Asserted by
    executing the real gate tail — the only channel a human or CI actually
    sees; a refusal that only logs would keep the pipeline green."""
    proc = _run_gate("1")
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    assert "ERROR: sweep did not complete credibly" in proc.stderr


def test_gate_is_not_taken_on_a_clean_run():
    """The gate must not fire for a healthy/no-op run (the tail ends in
    `exit 0`)."""
    proc = _run_gate("0")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "ERROR" not in proc.stderr


@pytest.mark.parametrize("body,exit_code,closed,not_closed", [
    # J1 failure journey: a refused enumeration exits non-zero and heals nothing.
    (_ENUM_FAILED_BODY, 1, (), ("APP_DOWN", "WATCHER_DOWN", "R2_DOWN")),
    # A sweep call that returned no credible status is the same class.
    (_MALFORMED_BODY, 1, (), ("APP_DOWN", "WATCHER_DOWN", "R2_DOWN")),
    # J2: a 0-team run closes what a completed round trip proves (fresh
    # heartbeat in this harness) and leaves nothing else open.
    (_NO_TEAMS_BODY, 0, ("APP_DOWN", "WATCHER_DOWN", "R2_DOWN"), ()),
    # A real run heals all three.
    (_BACKED_UP_BODY, 0, ("APP_DOWN", "WATCHER_DOWN", "R2_DOWN"), ()),
    # Benign no-op shapes never heal anything and never go red.
    (_NO_ELIGIBLE_TEAMS_BODY, 0, ("APP_DOWN", "WATCHER_DOWN", "R2_DOWN"), ()),
    (_NO_WORK_BODY, 0, ("APP_DOWN", "WATCHER_DOWN", "R2_DOWN"), ()),
    # A corroboration failure is red and heals nothing.
    (_NO_WORK_WITH_ERRORS_BODY, 1, (), ("APP_DOWN", "WATCHER_DOWN", "R2_DOWN")),
    (_NO_TEAMS_NO_SOURCE_BODY, 1, (), ("APP_DOWN", "WATCHER_DOWN", "R2_DOWN")),
])
def test_driver_chain_end_to_end(body, exit_code, closed, not_closed):
    """#2823 sequencing: the sweep RESPONSE drives the heal set AND the run's
    exit code inside one real execution — the coupling three independently
    pinned literals could not verify."""
    proc = _run_chain(body)
    assert proc.returncode == exit_code, (proc.returncode, proc.stdout, proc.stderr)
    for kind in closed:
        assert f"CLOSE OPEN_{kind}" in proc.stdout, (kind, proc.stdout)
    for kind in not_closed:
        assert f"CLOSE OPEN_{kind}" not in proc.stdout, (kind, proc.stdout)


def test_sweep_step_posts_the_sweep_endpoint(tmp_path):
    """J1's REQUEST contract: the harness stubs `curl`, so without capturing its
    arguments a wrong/changed target URL would go unnoticed — the driver would
    still parse a canned body correctly."""
    log = tmp_path / "curl.txt"
    _run_sweep_step(_NO_TEAMS_BODY, curl_log=log)
    call = log.read_text()
    assert "/v1/internal/backups/sweep" in call, call
    assert "-X POST" in call, call
    assert "-d {}" in call, call
