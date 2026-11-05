#!/usr/bin/env python3
"""rail_detach.py — make the merge rail OUTLIVE the lane that starts it.

THE DEFECT THIS EXISTS FOR (measured 2026-11-03)
------------------------------------------------
`scripts/admin-merge.sh` is the mandated merge chokepoint. Once it has computed
its evidence it WAITS for the PR's CI run to finish — and how long that takes is
set by the runner QUEUE, not by the lane:

    run 41557371710 (`Python CI`, 29 jobs)   created 01:05:15Z
                                             still queued at 02:56Z  (>= 6660 s)
    lane budget that killed it               1700 s  (lane died 02:09:32Z)
    the rail's own ceiling                   18064 s (derived; 2 x slowest green)

So the rail was healthy-and-waiting when the LANE's harness timeout killed it.
Nothing was wrong with the rail's verdict — the verdict never got to exist,
because it was going to be delivered by a process whose lifetime was the lane's.
An actuator that dies before its work finishes is not an actuator.

THE SHAPE (and why it is not a second lander, #1178)
----------------------------------------------------
This tool CONTAINS NO MERGE LOGIC AT ALL. It launches `scripts/admin-merge.sh`
as it is, with its arguments, and nothing else: every gate (tested-head, moved
head, baseline, evaluated tree, exemption decision, review attestation) stays in
the one lander. Two verbs:

    start <PR>    detach the rail into its own SESSION (setsid), with a pid, its
                  own log, and a durable artifact it owns; return immediately.
    collect <PR>  report the artifact — `RAIL(<pr>, <state>, <run id>, <since>)`.
                  Read-only: no merge, no comment, no re-run, no writes.

A later lane, or the next fleet beat, can therefore `collect` a verdict that a
dead lane started. Detachment changes WHO OUTLIVES WHOM, never WHAT IS CHECKED.

THE ARTIFACT (durable, tool-owned)
----------------------------------
    ${CELL_HOME:-$HOME}/.pi/agent/state/rail/rail-<owner-repo>-<pr>.md

`KEY=VALUE` lines, appended in place: PR/REPO/HEAD/STARTED_AT/PID/SID/LOG/CWD,
then `CHILD_PID=` (written by the RAIL itself), `CHILD_STARTED=` and
`CHILD_START_TICKS=` (the rail's own `/proc` starttime, the step-immune identity
anchor), then `EXIT=`/`FINISHED_AT=` when the rail exits. `VERDICT` and `MERGE_SHA` are
DERIVED at collect time from the rail's own log lines (`✅ merged PR #N at <sha>`,
`ALREADY MERGED`) rather than re-asking the API — the artifact must be readable
when the network is not, and the rail's own words are the primary record.

STOPPING A RAIL (the artifact is a record, not an off switch)
-------------------------------------------------------------
`rm <artifact>` clears the CLAIM and stops NOTHING — both refusal hints offer it
because it is how a stale claim is released, but the rail it names keeps waiting
and keeps merging, and the freed claim lets the next `start` put a SECOND rail on
the same PR (#3061). To actually stop one, signal its process GROUP: `start` runs
the wrapper under `setsid`, so `PID=` is the group leader and the rail it forked
is in that group, which makes `kill -TERM -<PID>` (negative = group) reach both.
The wrapper then never writes `EXIT=`, so `collect` answers `lost` — or `merged`
if the rail had already printed its success line, since that line outranks `lost`.
Stopping a rail does not un-merge anything; it only ends the process that would
have reported it.

STATES
------
Two INDEPENDENT inputs decide the state — this attempt's `EXIT=` line and the
rail's own words in the attempt-scoped log — and an exit code alone never makes
a merge (see `_merge_evidence`):

    waiting   no `EXIT=` yet, and something that carries the rail — the wrapper,
              the rail itself, or the process group in the fork window — is alive.
              ⛔ ALIVE OUTRANKS ANNOUNCED: a rail that has printed its success
              line but is still running (its post-merge bookkeeping) reads `waiting`
              here, because the announcement only becomes a verdict once nothing
              that carries it is left. An announced merge beats `lost`; it never
              beats `waiting`. (`collect --assert-merged` therefore stays 1 while
              the rail lives — the gate refuses to certify an in-flight merge.)
    merged    the log carries the rail's OWN success line (`✅ merged PR #N at
              <sha>`, `✅ merged PR #N (state=` or `PR #N is ALREADY MERGED`),
              whether or not an `EXIT=` line ever landed — and nothing that
              carries the rail is still alive
    refused   an `EXIT=` line with NO merge line in the log: `EXIT=0` without a
              merge line is a DECLINED merge (`--dry-run`, `--help`), not a merge
    lost      nothing that carries the verdict is left and there is no `EXIT=`
              line — killed outright. A vanished rail's absence is never read as
              a merge; inventing one is the #2235 fail-open shape. But a merge
              the rail already ANNOUNCED outranks `lost`: that line is printed
              only after the API confirmed `state=MERGED`, so only the bookkeeping
              was lost, not the verdict.

WHICH PROCESS *IS* THE RAIL (measured 2026-11-03, review P1)
-----------------------------------------------------------
`start` spawns `bash -c <wrapper>`; the wrapper starts the rail as a BACKGROUND
job (so it can `wait` for it and then write `EXIT=`). Two processes therefore
carry the work, and they do NOT die together:

    PID=        the wrapper — writes `EXIT=`, dies first when someone kills it
    CHILD_PID=  the rail itself — runs `scripts/admin-merge.sh`

Probing only `PID=` reported `lost` for a rail that had merely lost its wrapper:
measured, the wrapper took a SIGKILL, `killpg(pid, 0)` still answered, the rail
ran to completion and printed its merge line, and nobody was left to write
`EXIT=`. The next `start` was then allowed, truncated the artifact and appended a
new attempt mark — which scopes the FIRST rail's `✅ merged` line out for good.
A wrong, durable verdict for a PR that merged.

So a rail is alive while EITHER process is, and the rail's own pid is recorded BY
THE RAIL (`$$`, before it execs `admin-merge.sh`) rather than by the wrapper
(`$!`): a wrapper killed at any moment after the fork then still leaves a name
for a rail that is running. The rail's claim is checked by GROUP MEMBERSHIP
(`os.getpgid(child) == the wrapper's pid`), not by its argv: a pid's session and
process group are inherited across fork and preserved across `execve`
(credentials(7)), so membership survives whatever the rail execs, and a recycled
pid — which belongs to some other group — cannot impersonate it.

"gone" has to be TRUE on every box this runs on, and it is not free: on a host
without procfs `os.kill(pid, 0)` answers yes for a ZOMBIE. `_pid_alive` therefore
reads the state letter from procfs where it exists and from a bounded local `ps`
probe where it does not, so a killed rail reads `lost` on macOS too.

WHY NOT `nohup`/`&`
-------------------
`&` inherits the lane's process GROUP, so the harness's group kill takes the rail
with it; `nohup` only ignores SIGHUP, it does not leave the group. `setsid`
(here via `start_new_session=True`) puts the rail in its own session, which is
what survives both the SIGKILL to the lane's group and the lane process exiting.
`tests/test_rail_detach.py` proves the difference by re-running the SAME launch
with detachment off and killing the launcher's group.

Stdlib only (this runs on the plain `python3` of a cell — which must be >= 3.12;
see the guard below).

EXIT CODES
----------
    0  ok — a verdict was reported (`waiting`/`merged`/`refused`/`lost`)
    1  `collect --assert-merged` and the state is not `merged` — the machine gate
    2  environment error: `collect` could not name the repo, `start` could not
       spawn the rail (EAGAIN/ENOMEM/a vanished CWD), or the interpreter is < 3.12
    3  `collect` found no artifact for that PR (answered from disk — no `gh`)
    4  `start` REFUSED — a rail is already live, a forbidden bypass is set, the
       rail script is missing, or the repo could not be named. ⛔ The repo case is
       DELIBERATELY 4 here and 2 in `collect`: `collect`'s 2 is a failed
       environment read, while `start`'s is a precondition the launcher enforces
       and names. Neither is 1 — 1 is the machine gate, never an environment.
       (Stated per verb because one table for both verbs was wrong for `start`.)
"""
from __future__ import annotations

import sys

# #5128 shape: refuse a <3.12 interpreter BEFORE the imports below. This is not a
# style rule — the module imports `datetime.UTC` (3.11+), so on an older `python3`
# (the ambient /usr/bin/python3 on a macOS box is 3.9) the import dies FIRST and
# the operator gets a traceback instead of this message. The gate that enforces
# the position is tests/test_entry_point_python_guard.py, corpus-wide.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # #5128/#4053: emit the message explicitly and exit 2. `SystemExit(<str>)`
    # always exits 1, and THIS tool publishes 1 as `collect --assert-merged`
    # "not merged" — the fleet's machine gate. An old interpreter reported
    # through 1 would be indistinguishable from a rail that ran and refused,
    # so 2 (this tool's environment-error code) carries the refusal instead.
    print(
        f"tools/rail_detach.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/rail_detach.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import fcntl
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# ── the mutation seam ────────────────────────────────────────────────────────
# The ONLY difference between the rail surviving its lane and dying with it.
# `tests/test_rail_detach.py` patches this to False and observes the rail die —
# a test that passes either way would prove nothing.
DETACH_NEW_SESSION = True

# How long an un-pid'd `SPAWNING=1` artifact may stand for "launching". A spawn
# happens in microseconds; a minute is generous enough to never race a healthy
# launch and short enough that a launch killed in that window cannot wedge a PR.
SPAWNING_TTL_S = 60
# How long a process GROUP that still answers `killpg(..., 0)` may stand for "a
# rail is in it" when nothing in it has NAMED itself (`CHILD_PID=`). The rail
# writes its own pid a few instructions after the fork, so the healthy window is
# microseconds. Past the grace the group's only member is a ZOMBIE (killpg
# answers for a zombie forever) or a recycled pid — and `waiting` there is the
# unrailable wedge this module exists to remove: `lost` is both true and
# recoverable. Same duration as the spawn window, for the same reason.
GROUP_CLAIM_GRACE_S = SPAWNING_TTL_S
# How far a pid's own start time may exceed the artifact's `CHILD_STARTED=` before
# it is provably a different process — used ONLY on the wall-clock fallback, i.e. a
# box with no procfs (macOS), because there the rail cannot record
# `CHILD_START_TICKS=` (see `child_script`) and identity has to be reconstructed from
# timestamps. The stamp has 1-second resolution and is written immediately after the
# fork, so the slack covers that plus a forward wall STEP of up to this size — a
# step is the residual failure mode on this path, and it can only ever make a rail
# look NEWER. Deliberately its own literal, NOT the spawn TTL: the two quantities
# are unrelated, and aliasing them would silently widen one when the other moves.
IDENTITY_SLACK_S = 5
# Field 22 of `/proc/<pid>/stat` is `starttime`, in clock ticks since boot. It is
# the kernel's monotonic record of when that pid started: it does not move with the
# wall clock, so comparing it is exact — no slack, no step sensitivity — which is
# why the ticks path is preferred over the timestamp path wherever procfs answers.
PROC_STARTTIME_FIELD = 22

# Every `gh` call here is INFORMATIONAL and every one of them is bounded. An
# unbounded one is not a slow read, it is a WEDGE: `_head_sha` runs while this
# process holds the duplicate-rail flock, so a `gh` stalled on a saturating cell
# blocks that lock and every later `start` for the PR queues behind it — the
# guard becomes the thing that makes the PR unrailable.
#
# ⛔ AND THE CLI `collect` RESOLVES ITS REPO FROM DISK, NOT FROM `gh` — except in
# ONE case, stated rather than glossed: when the state dir holds TWO OR MORE
# artifacts for the same PR number under different repos, the PR number alone does
# not name the target, so `main` falls back to `_resolve_repo` (a bounded `gh`).
# ZERO candidates and ONE candidate are both answered from disk — zero is `absent`
# (3) without naming any repo at all, because an artifact that no repo's filename
# matches cannot exist for ANY repo. This used to be false for zero: the CLI asked
# `gh`, and with no reachable `gh` it exited 2 (environment error) where the exit
# table promises 3 (`collect` found no artifact) — a status read that failed
# because the API did, which is exactly what this verb exists not to do.
GH_PROBE_TIMEOUT_S = 10
# The local liveness probe (`ps`), used only where there is no procfs. Local,
# so this is a runaway guard rather than a network budget.
PS_PROBE_TIMEOUT_S = 5

RAIL_REL = "scripts/admin-merge.sh"
DEFAULT_STATE_DIR = Path(
    os.environ.get("CELL_HOME") or os.path.expanduser("~")
) / ".pi" / "agent" / "state" / "rail"

# ⛔ The forbidden escape hatch, by name. A launcher that CARRIED this into a
# detached rail would detach a bypass — the one thing detachment must never do.
FORBIDDEN_ENV = ("AGENT_ADMIN_MERGE_OVERRIDE",)

RUN_ID_RE = re.compile(r"\brun (\d{5,})\b")
MERGED_AT_RE = re.compile(r"✅ merged PR #(\d+) at ([0-9a-f]{7,40})")
# admin-merge.sh's `✅ merged PR #N (state=…` success line — the SAME success with
# the merge commit deliberately not
# printed (it was unreadable). A merge with no sha is still a merge; the sha is
# simply absent, and the artifact says so rather than substituting the head.
MERGED_NO_SHA_RE = re.compile(r"✅ merged PR #(\d+) \(state=")
ATTEMPT_MARK = "===== rail attempt started"
# ⛔ THE MARK NAMES ITS ATTEMPT, because the mark is the EVIDENCE BOUNDARY.
# Scoping the log read by "the last mark in the file" leaves one window: `launch`
# truncates the artifact and writes the new `ATTEMPT_ID=` BEFORE its own mark
# lands, so a `collect` running in that instant pairs the NEW attempt with the
# PREVIOUS attempt's tail — and a previous attempt's `✅ merged` line then reads
# as this attempt's completed merge (a merge claimed for an attempt that never
# announced one). Carrying the id in the mark makes the boundary attributable:
# a mark whose id is not ours bounds nothing.
ATTEMPT_MARK_ID_RE = re.compile(r"\battempt=(\S+)")
REPO_EVIDENCE_RE = re.compile(r"admin-merge: repo (\S+) \(resolved from")
ALREADY_MERGED_RE = re.compile(r"PR #(\d+) is ALREADY MERGED")
REFUSAL_RE = re.compile(r"(?:✗|BLOCK|REFUSING|REFUSED)[^\n]*")

WAITING, MERGED, REFUSED, LOST = "waiting", "merged", "refused", "lost"

# Memoised answer to "can this box's `ps` report a pid's state letter?".
_PS_USABLE: bool | None = None


# ── paths ────────────────────────────────────────────────────────────────────
def _slug(repo: str) -> str:
    """The artifact/lock name for a repo — ONE name per repo, whatever its spelling.

    ⛔ THE DUPLICATE-RAIL GUARD IS THE LOCK, AND A LOCK IS ONLY AS GOOD AS THE
    IDENTITY IT IS KEYED ON (`flock(2)`: locks are held on the open FILE, so two
    spellings that produce two paths are two locks). Keying this on the raw repo
    string made `o/r` and `github.com/o/r` two different artifacts — measured, both
    `launch()` calls started and ran concurrently, i.e. TWO LIVE RAILS ON ONE PR,
    the #3061 collision the guard exists to refuse, while a third launch with the
    SAME spelling was correctly refused.

    So the name is derived from `_repo_key` — the module's ONE canonicaliser, which
    lowercases and drops an attestable host prefix — and every consumer of this
    function (`artifact_path`, `log_path`, and the `.lock` which is derived from the
    artifact) inherits the single key without repeating it.
    """
    return _repo_key(repo).replace("/", "-")


def artifact_path(state_dir: Path, repo: str, pr: int) -> Path:
    return Path(state_dir) / f"rail-{_slug(repo)}-{pr}.md"


def log_path(state_dir: Path, repo: str, pr: int) -> Path:
    return Path(state_dir) / f"rail-{_slug(repo)}-{pr}.log"


# ── artifact I/O ─────────────────────────────────────────────────────────────
def read_field(text: str, key: str, default: str = "") -> str:
    """First `KEY=VALUE` line for KEY (the artifact is append-only, kv lines)."""
    for line in text.splitlines():
        if line.startswith(key + "="):
            return line[len(key) + 1:]
    return default


def read_all(text: str, key: str) -> list[str]:
    return [ln[len(key) + 1:] for ln in text.splitlines() if ln.startswith(key + "=")]


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_seconds(iso: str) -> float | None:
    """Seconds since an artifact timestamp, or None when it cannot be read."""
    try:
        then = datetime.strptime(iso.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC)
    except (AttributeError, ValueError):
        return None
    return (datetime.now(UTC) - then).total_seconds()


def _ps_stat(pid: int) -> str | None:
    """The `stat` field `ps` reports for `pid` ('' when it knows no such pid),
    or None when the probe itself cannot be trusted.

    ⛔ WHY THIS EXISTS: `os.kill(pid, 0)` SUCCEEDS ON A ZOMBIE. On a box with
    procfs the `/proc/<pid>/stat` state letter answers that, but the fallback
    used to be "kill(0) said yes, so it is alive" — which on a box WITHOUT
    procfs (macOS, where the lanes run) made a SIGKILLed rail read `waiting`
    FOREVER: `lost` was unreachable, and the duplicate-rail guard then refused
    every later `start` on that PR. Measured 2026-11-03: a killed rail's bash
    still answers kill(0), `ps -o stat=` reports `Z`, and the PR never
    recovers.

    FAIL DIRECTION IS `alive`: an unreadable probe returns None and the caller
    keeps reporting the rail as live. Reading a live rail as dead would let a
    SECOND rail through (#3061); reading a dead one as live only leaves the
    existing wedge, now bounded by the rest of this module's recovery paths.
    """
    out = _ps("-o", "pid=,stat=", "-p", str(pid))
    if out is None:
        return None
    fields = out.split()
    if len(fields) < 2 or fields[0] != str(pid):
        # Either `ps` found nothing or it is not speaking the format we asked
        # for. Tell those apart before concluding "gone".
        return "" if _ps_usable() else None
    return fields[1]


def _ps(*args: str) -> str | None:
    """`ps` stdout, or None when it could not be run/parsed at all."""
    try:
        out = subprocess.run(
            ["ps", *args], capture_output=True, text=True,
            timeout=PS_PROBE_TIMEOUT_S,
            # ⛔ PIN THE LOCALE: `lstart` is a HUMAN-READABLE date, and `_pid_started_epoch`
            # parses its month name. Under a localized `LC_TIME` (es_ES and friends) every
            # parse would fail, `_pid_started_epoch` would answer None, and the
            # recycled-pid identity test would silently degrade to "keep the claim" on
            # exactly the boxes this fallback exists for. Pinned here so the claim that the
            # parse is C-locale is a fact rather than an assumption.
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout


def _ps_usable() -> bool:
    """Does `ps` answer for a pid it must be able to see (our own)?

    A bare `ps -p <missing>` exit code cannot tell "no such process" from "this
    ps does not accept that format" — macOS returns 1 for BOTH (measured), so
    trusting it would read a live rail as dead on a box with a different `ps`.
    Memoised: this costs at most one extra probe per process.
    """
    global _PS_USABLE
    if _PS_USABLE is None:
        out = _ps("-o", "pid=,stat=", "-p", str(os.getpid()))
        fields = (out or "").split()
        _PS_USABLE = len(fields) >= 2 and fields[0] == str(os.getpid())
    return _PS_USABLE


def _proc_stat_letter(pid: int) -> str | None:
    """The state letter from `/proc/<pid>/stat`, or None where there is no
    procfs (macOS) / it cannot be read."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(") ", 1)[1].split()[0]
    except (OSError, IndexError):
        return None


def _proc_start_ticks(pid: int) -> int | None:
    """`/proc/<pid>/stat` field 22 (`starttime`, clock ticks since boot), or None
    where there is no procfs.

    This is the identity anchor because it is MONOTONIC: it is stamped by the
    kernel at fork and never moves, so one process always answers the same value
    and a recycled pid always answers a different one. Wall-clock timestamps
    cannot do this — `btime` moves with `settimeofday`, so a forward step makes a
    LIVE process look newer than it is (the fail-danger direction here).
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        # Split after the LAST `) `: the comm field may contain spaces AND parens,
        # so a positional split over the whole line reads the wrong field — the same
        # reason `_proc_stat_letter` does it this way. Field 3 (state) is element 0,
        # so field 22 is element 19.
        return int(raw.rsplit(") ", 1)[1].split()[PROC_STARTTIME_FIELD - 3])
    except (OSError, IndexError, ValueError):
        return None


def _pid_started_epoch(pid: int) -> float | None:
    """When `pid`'s process started, as a Unix EPOCH in the CURRENT wall clock, or
    None when unmeasurable.

    ⛔ ONLY A FALLBACK, and only where procfs cannot answer (macOS): the epoch is
    wall-clock derived (`btime` + ticks on Linux, `lstart` otherwise), so a clock
    STEP after the artifact's stamp shifts it. `_pid_is_a_stranger` therefore tries
    the monotonic ticks FIRST and lands here only when they are unavailable.

    procfs answers exactly (field 22 ticks + `btime`); without it the local `ps` is
    asked for `lstart` in the C locale. Both are bounded (`PS_PROBE_TIMEOUT_S`);
    anything unparseable is None and the caller keeps the claim.
    """
    try:
        starttime = _proc_start_ticks(pid)
        if starttime is None:
            raise OSError("no procfs")
        hz = os.sysconf("SC_CLK_TCK")
        btime = None
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                btime = int(line.split()[1])
                break
        if btime is None:
            return None
        return btime + starttime / hz
    except (OSError, IndexError, ValueError):
        pass
    out = _ps("-o", "lstart=", "-p", str(pid))
    if not out or not out.strip():
        return None
    try:
        # `lstart` is LOCAL time in the C locale — `_ps` pins `LC_ALL=C` so the
        # month name is parseable; `ps` pads the day ("Nov  3") and strptime
        # collapses the run of whitespace for us.
        return time.mktime(time.strptime(" ".join(out.split()),
                                         "%a %b %d %H:%M:%S %Y"))
    except (ValueError, OverflowError):
        return None


def _pid_is_a_stranger(pid: int, recorded_iso: str,
                       recorded_ticks: str = "") -> bool:
    """True ONLY when `pid` provably belongs to a process that started AFTER the
    rail was forked — i.e. the rail's pid number was RECYCLED.

    ⛔ ONE-SIDED BY DESIGN: it can conclude "this is not our rail" and never "this
    is", so an unmeasurable probe returns False (keep the claim) — the same
    fail-direction discipline as `_ps_stat` and `_in_group`. It therefore cannot
    produce a false `lost`, and it never expires a LIVE rail however long the
    runner queue takes: the rail's own start record is fixed and no elapsed time
    ever changes it.

    TWO EVIDENCE SOURCES, strongest first:

    1. `CHILD_START_TICKS=` (written from the rail's own `/proc` entry) against the
       probed pid's ticks — MONOTONIC, so it is exact: equal ⇒ same process,
       different ⇒ recycled. No slack, and no wall-clock sensitivity, which is what
       keeps a clock STEP from reading a live rail as `lost`.
    2. `CHILD_STARTED=` (a wall timestamp) against the probed pid's start EPOCH —
       only where there is no procfs. This path IS step-sensitive by construction:
       a forward step larger than `IDENTITY_SLACK_S` makes a live rail look newer
       than its stamp, i.e. a stranger. That is a known, documented residual on the
       no-procfs box class (the rail cannot read its own ticks there), and it is
       the reason the slack exists rather than a tight bound.

    Either source unmeasurable ⇒ False (keep the claim).
    """
    if pid <= 0:
        return False        # nothing to test ⇒ keep the claim, per the contract above
    recorded = _int_or_none(recorded_ticks)
    current = _proc_start_ticks(pid)
    if recorded is not None and current is not None:
        return current != recorded
    started = _pid_started_epoch(pid)
    if started is None:
        return False
    age = _age_seconds(recorded_iso)
    if age is None:
        return False
    return started > (time.time() - age) + IDENTITY_SLACK_S


def _proc_cmdline(pid: int) -> str | None:
    """`/proc/<pid>/cmdline` as text, or None where procfs does not answer."""
    cmdline = Path(f"/proc/{pid}/cmdline")
    if not cmdline.exists():
        return None
    try:
        return cmdline.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return None


def _in_group(pid: int, pgid: int) -> bool:
    """`pid` exists AND is a member of `pgid`'s process group.

    THE ownership test for the rail process. It survives `exec` (fork and execve
    both preserve the group) where an argv marker does not — the rail may exec
    anything — and it defeats pid reuse, because a recycled pid is in some other
    group. `getpgid` raises for a pid that is gone, so this also answers "exists".
    ONE-SIDED, like every probe here: a denied probe (`PermissionError`) answers
    True, because "I may not look" is not "it is not ours".
    """
    if pid <= 0 or pgid <= 0:
        return False
    try:
        return os.getpgid(pid) == pgid
    # ⛔ THE ERRNO IS THE WHOLE ANSWER, so only ESRCH is "gone".
    # `getpgid` raises `PermissionError` for a process that EXISTS and that we may
    # not inspect, and a blanket `except OSError` reads that as "not ours" — the
    # `lost` direction this module forbids (`_rail_alive`'s killpg arm states the
    # same rule 100 lines below, and `psutil.pid_exists` is the canonical
    # implementation: True on PermissionError, False only on ProcessLookupError).
    # Unmeasurable ⇒ keep the claim, exactly as `_ps_stat` and `_pid_is_a_stranger`
    # do, because `lost` is the state `start` ACCEPTS: reading a live rail as dead
    # lets a SECOND rail through on a PR that is still merging (#3061).
    except ProcessLookupError:
        return False
    except OSError:
        return True


def _group_has_a_live_member(pgid: int) -> bool | None:
    """Is there a process in `pgid` that is NOT a zombie? None when the probe
    itself cannot answer.

    ⛔ WHY IT EXISTS. `killpg(pgid, 0)` answers for a group whose only remaining
    member is a ZOMBIE — and a zombie can never write `CHILD_PID=`/`EXIT=`, so a
    claim bounded by that answer alone is the unrailable wedge. But the bound
    cannot be AGE (see the caller): a live rail that has not yet executed its
    self-naming `printf` is in that same group, so an age bound reads a LIVE,
    MERGING rail `lost` — the state `start` accepts, i.e. a second rail (#3061).
    The group's MEMBERS settle it: zombies only ⇒ nothing can ever write again;
    anything else ⇒ the rail's work is still running.

    State is field 3 and the process group is field 5 of `/proc/<pid>/stat` where
    procfs answers; where it does not (macOS lanes) the same two columns come from
    a bounded `ps`. FAIL DIRECTION IS `alive`: an unlistable or unparseable probe
    returns None, and the caller keeps the claim (`_ps_stat`'s rule).

    ⛔ A STAT WE CANNOT READ IS NOT A MEMBER OF OUR GROUP. Every member of a group
    led by our own `bash -c` child carries our uid — `fork`/`execve` preserve
    credentials and this rail never changes them — so `/proc/<pid>/stat` is
    readable for it even under `hidepid=1` (which raises EACCES only for OTHER
    users' entries; `hidepid=2` does not list them at all). Counting those as
    "unmeasurable" would make the answer permanently "unknown" on any box running
    other users' processes — the wedge again, in the other direction.
    """
    if pgid <= 0:
        return False
    try:
        has_procfs = Path("/proc/self/stat").exists()
    except OSError:
        has_procfs = False
    if has_procfs:
        try:
            entries = os.listdir("/proc")
        except OSError:
            return None
        for name in entries:
            if not name.isdigit():
                continue
            try:
                rest = Path(f"/proc/{name}/stat").read_text().rsplit(") ", 1)[1].split()
            except (OSError, IndexError):
                continue          # not ours to read ⇒ cannot be in our group
            if len(rest) < 3 or rest[2] != str(pgid):
                continue
            if rest[0] != "Z":
                return True
        return False
    out = _ps("-A", "-o", "pid=,pgid=,stat=")
    if out is None:
        return None
    # ⛔ PROVE THE PROBE CAN SPEAK THIS FORMAT BEFORE AN EMPTY ANSWER MEANS "EMPTY".
    # `_ps` returns stdout whether or not `ps` ACCEPTED the request, so a `ps` that
    # rejects `pid=,pgid=,stat=` (or answers header-only / with other columns) hands
    # back an empty string — and falling through to `False` below would report a
    # LIVE rail's group as empty, i.e. `lost` for a rail that is still merging: the
    # fail-danger direction this whole arm exists to prevent. `_ps_stat` already
    # carries this rule ("macOS returns 1 for BOTH — trusting it would read a live
    # rail as dead on a box with a different `ps`"); it is the SAME box class, so the
    # same answer: ask for the one pid the probe MUST be able to see (ours) in the
    # same format, and treat a miss as unmeasurable rather than as evidence.
    own = _ps("-o", "pid=,pgid=,stat=", "-p", str(os.getpid()))
    fields = (own or "").split()
    if len(fields) < 3 or fields[0] != str(os.getpid()):
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[1] != str(pgid):
            continue
        if not parts[2].startswith("Z"):
            return True
    return False


def _pid_alive(pid: int, marker: str = "", *, pgid: int | None = None) -> bool:
    """Alive AND still ours — `os.kill(pid, 0)` alone reports a RECYCLED pid as
    a live rail, which would report `waiting` forever on a dead rail.

    `pgid` swaps the "is it ours" test from an argv marker to GROUP MEMBERSHIP —
    required for the rail itself (see the module docstring): its argv is whatever
    `admin-merge.sh` last exec'd, while its group is fixed at fork time.

    `marker=""` with no `pgid` is a LIVENESS-ONLY probe — there is nothing in the
    ARGV to test identity against (the `CHILD_PID` of an artifact whose wrapper pid
    was never recorded). Its residual is that a RECYCLED pid reads `waiting`, which
    is why the `collect` caller pairs it with `_pid_is_a_stranger`: the pid's START
    TIME against the artifact's `CHILD_STARTED=` settles recycling wherever that
    stamp exists. It is deliberately NOT aged out — the rail's wait is unbounded, so
    a TTL would read a live merge as `lost` and let a second rail through (#3061).
    Where the stamp is absent nothing can settle identity, so the claim is kept and
    `collect`'s `reason` (and `start`'s refusal) name the one way out: `rm` the
    artifact.
    """
    if pid <= 0:
        return False
    if pgid is None:
        try:
            os.kill(pid, 0)
        # ⛔ SAME THREE-VALUED ANSWER AS `_in_group` AND `_rail_alive`'s killpg arm:
        # `PermissionError` means the process EXISTS and we may not signal it, i.e.
        # a rail that is still merging. Only ESRCH is `gone`; anything else is
        # unmeasurable, and unmeasurable keeps the claim (`_ps_stat`) — reading it
        # as `lost` releases `start` on a live rail (#3061).
        except ProcessLookupError:
            return False
        except OSError:
            return True
    elif not _in_group(pid, pgid):
        return False
    # A ZOMBIE still answers kill(0) and its cmdline is EMPTY — reporting it alive
    # would leave a killed rail reading `waiting` forever. `Z` is gone-as-far-as-
    # a-verdict-is-concerned.
    proc_letter = _proc_stat_letter(pid)
    if proc_letter == "Z":
        return False
    raw = _proc_cmdline(pid)
    if raw is not None:
        # An EMPTY cmdline is a process that exists but has not exec'd yet (the
        # fork/exec window) — alive, not dead. Reporting `lost` there is how a
        # healthy rail reads as killed (#observed live 2026-11-03).
        if not raw.strip():
            return True
        if pgid is not None:
            # Identity is already settled by group membership; the argv is the
            # rail's own business and may be anything it exec'd.
            return True
        if not marker:
            # LIVENESS-ONLY probe: the caller has no identity to test against (the
            # `SPAWNING`/`CHILD_PID` case, where the launcher died before recording
            # the wrapper pid and there is no group to join). Existence + not-a-
            # zombie is the whole answer.
            return True
        # The child's argv carries the rail path; a recycled pid will not.
        return Path(marker).name in raw or marker in raw
    if proc_letter is not None:
        # procfs answers but the cmdline did not: the process is real and not a
        # zombie, and its argv was unreadable — keep the rail's claim.
        return True
    # ⛔ NO PROCFS: the kill probe above is NOT enough — it answers yes for a
    # zombie, so a killed rail would read `waiting` forever (see `_ps_stat`).
    stat = _ps_stat(pid)
    if stat is None:
        return True  # unmeasurable ⇒ keep the rail's claim alive (see _ps_stat)
    if not stat:
        return False  # `ps` knows no such pid ⇒ it is gone
    if stat.startswith("Z"):
        return False
    if pgid is not None:
        # Identity is already settled by group membership; the argv is the
        # rail's own business and may be anything it exec'd.
        return True
    if not marker:
        return True   # nothing to test identity against; keep the claim
    # ⛔ A RECYCLED PID ON A BOX WITHOUT PROCFS. The `pid` here is the `bash -c`
    # WRAPPER, which NEVER execs — its argv carries the artifact path for its
    # whole life — so the marker IS a usable identity test for it. (It is not
    # for the RAIL, which execs; that is what `pgid` is for, and the arms above
    # say so.) Without this check a recycled pid reads `waiting` forever: it is
    # reported alive AND blocks every later `start` on the PR — the unrailable
    # wedge this module exists to remove, and the one thing `SPAWNING_TTL_S`
    # cannot age out.
    # ⛔ `-ww`: BSD/macOS `ps` truncates `command=` to the DISPLAY width (80 when
    # stdout is not a tty), which cuts the artifact path off before the marker —
    # so a LIVE wrapper reads as "not ours" and `lost` is drawn from it. The flag
    # is what makes the marker readable at all on the box class this module says
    # lanes run on (macOS lanes, no procfs ⇒ this is the ONLY identity probe).
    cmd = _ps("-ww", "-o", "command=", "-p", str(pid))
    if cmd is None:
        return True   # the probe could not answer ⇒ keep the claim
    return Path(marker).name in cmd or marker in cmd


def _rail_alive(leader: int, child: int, marker: str,
                *, age: float | None = None) -> bool:
    """Is the rail's WORK still running? Two processes carry it, and they do not
    die together: the wrapper (`leader`, which writes `EXIT=`) and the rail
    itself (`child`, which runs `admin-merge.sh`). Probing only the wrapper read
    `lost` for a rail that had merely lost its wrapper and was still merging
    (measured — see the module docstring). Either one alive means `waiting`.

    An artifact from before `CHILD_PID=` has no rail pid; it falls back to the
    wrapper-only probe, which is what it was written under.

    `age` is the attempt's age. It bounds ONLY the group fallback (see
    `GROUP_CLAIM_GRACE_S`): `killpg(..., 0)` answers for a ZOMBIE group member
    forever, so an unbounded fallback reports `waiting` for a rail that will never
    write again and refuses every later `start`. A caller that passes no age (a
    direct probe) keeps the unbounded claim.
    """
    if _pid_alive(leader, marker):
        return True
    if leader > 0 and child > 0 and _pid_alive(child, pgid=leader):
        return True
    # ⛔ THE FORK → `CHILD_PID=` WINDOW. The rail is forked by the wrapper and
    # records its own pid a few instructions LATER; a wrapper killed in between
    # leaves `child == 0` naming nothing, while the rail is alive and merging.
    # Reading `lost` there is the exact P1 this module fixed: the next `start`
    # truncates the artifact and appends an attempt mark that scopes a real
    # `✅ merged` line out for good — a wrong, durable verdict for a merged PR.
    # The GROUP still answers, and the rail is in the wrapper's group BY
    # CONSTRUCTION (`start_new_session=True`, no job control in `bash -c`).
    if child <= 0 and leader > 0:
        # ⛔ `killpg` ANSWERS A THREE-VALUED QUESTION, so only ESRCH is "gone".
        # A blanket `suppress(OSError)` reads `PermissionError` as "gone" — but
        # that errno means the group EXISTS and we may not signal it, i.e. a rail
        # that is still merging. Reading it as `lost` is the durable-wrong-verdict
        # direction (the next `start` truncates the artifact and scopes a real
        # `✅ merged` line out). Any other OSError is unmeasurable, and the rule in
        # `_ps_stat` is unmeasurable ⇒ keep the claim.
        #
        # ⛔ AND IT MUST BE BOUNDED: a ZOMBIE group member keeps `killpg(..., 0)`
        # answering forever, so an unbounded answer here reports `waiting` for a
        # rail that will never write again and refuses every later `start` — the
        # wedge, not the guard.
        #
        # ⛔ BUT AGE IS THE WRONG BOUND. Elapsed time says nothing about whether
        # the rail is alive: a LIVE rail that has not YET executed its self-naming
        # `printf` sits in exactly the same group, and the rail's wait is set by
        # the runner QUEUE (unbounded — measured >= 6660 s). Expiring on the clock
        # read that rail `lost`, which `start` ACCEPTS: a SECOND rail on a PR that
        # is still merging (#3061), plus the artifact truncation that scopes its
        # real `✅ merged` line out (#2235). Measured before this probe existed: a
        # SIGKILLed wrapper with a live group member read `lost` at age 120 s and
        # `waiting` at age 0 — the age was the whole difference.
        #
        # So past the grace the group is expired on its MEMBERS' STATE, never on
        # the clock: zombies only ⇒ gone (`lost` is true and recoverable there);
        # anything else ⇒ the rail's work is still running ⇒ keep the claim.
        if age is not None and age > GROUP_CLAIM_GRACE_S:
            return _group_has_a_live_member(leader) is not False
        try:
            os.killpg(leader, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    return False


def _int_or_none(s: str) -> int | None:
    """A torn/garbled kv value must not crash the read-only verb. `printf` writes
    `EXIT=%s` and its argument in separate expansions, so a group kill can land
    between them and leave `EXIT=` — the fleet beat then gets a traceback instead
    of a state, which is worse than any verdict."""
    try:
        return int((s or "").strip())
    except (TypeError, ValueError):
        return None


def _attempt_exits(text: str) -> list[str]:
    """The `EXIT=` values written by the attempt THIS artifact belongs to.

    ⛔ THE HANDOFF HAS NO ORDERING. `launch` truncates the artifact while the
    PREVIOUS attempt's wrapper may still be about to append `EXIT=` — its rail
    printed the merge line first, so `collect` already answered `merged` and the
    new start was not refused. That late append then lands in the NEW artifact
    and reads as this attempt's exit: `collect` finds no merge line in the new
    (attempt-scoped) log and answers `refused` for an attempt that was KILLED — a
    durable WRONG verdict, the class this module exists to remove.

    So each attempt stamps its own id (`ATTEMPT_ID=` here, carried into the
    wrapper as `EXIT_ATTEMPT=`) and only a matching pair is evidence. An artifact
    written before this key has no id, so its `EXIT=` lines keep their old
    whole-file meaning — the compatibility direction is unchanged behaviour.
    """
    attempt = read_field(text, "ATTEMPT_ID")
    if not attempt:
        return read_all(text, "EXIT")
    out: list[str] = []
    tag = ""
    for line in text.splitlines():
        if line.startswith("EXIT_ATTEMPT="):
            tag = line[len("EXIT_ATTEMPT="):]
        elif line.startswith("EXIT="):
            if tag == attempt:
                out.append(line[len("EXIT="):])
            tag = ""   # an EXIT closes its tag; a later one is not ours
    return out


def _merge_evidence(log: str, pr: int, repo: str = "") -> tuple[str, str]:
    """(state, sha) from the rail's OWN words about THIS PR, or ('', '') when it
    said nothing usable.
    ⛔ A CODE-0 EXIT IS NOT A MERGE: `admin-merge.sh --dry-run` (and --help,
    --print-bounds) exit 0 having merged nothing, and the launcher deliberately
    passes operator args through. Merged requires the rail to SAY merged.

    ⛔ AND THE LINE MUST NAME THE PR BEING COLLECTED. Every success line carries
    its number, and the number was captured and then ignored — so a log whose one
    success line belongs to a DIFFERENT PR read as this PR's completed merge. It
    is reachable without a corrupted artifact: the launcher passes operator args
    through verbatim, and the rail's own argument loop takes the LAST `--repo`, so
    `start 12 -- --repo other/repo` produced a rail for `other/repo#12` under
    `o/r#12`'s artifact. Attribution only — the rail still only prints that line
    after the API confirmed the terminal state — but the verdict this module
    publishes is about the PR it was asked to collect, and "merged" for a PR that
    did not merge is the one claim #2235 forbids. A mismatched number is therefore
    NO EVIDENCE, which fails closed (refused/lost), never open.

    ⛔ AND THE NUMBER ALONE DOES NOT IDENTIFY A PR — THE REPO DOES. `start 12 --
    --repo other/repo` reaches the rail's passthrough, whose argument loop takes the
    LAST `--repo`, so the rail grades `other/repo#12` … and then prints `✅ merged
    PR #12`, whose NUMBER matches this artifact's. The number check cannot see that;
    the REPO can, because the rail prints the target it resolved on every run
    (`admin-merge.sh` `resolve_rail_repo`: `admin-merge: repo <owner/name> (resolved
    from <source>)`), and its own #2235 note says exactly why a bare number is not
    enough: it "exists in both". So, where that line is present, it must name the
    repo this artifact names. ONE-SIDED: no such line ⇒ nothing to contradict
    (legacy logs and stub rails keep working); a differing line ⇒ no evidence.
    """
    if not _names_this_repo(log, repo):
        return "", ""
    m = MERGED_AT_RE.search(log)
    if m:
        return (MERGED, m.group(2)) if _names_this_pr(m.group(1), pr) else ("", "")
    m = ALREADY_MERGED_RE.search(log)
    if m:
        # Already merged before this attempt: that merge commit belongs to the
        # earlier merge and is NOT this branch head (under a squash the head is
        # never on main — admin-merge.sh's `✅ merged PR #N (state=` line). Absent,
        # never invented. Cited by SYMBOL, not by line: a line cite re-stales every
        # time that file grows, and this one had (it said :9033 while the line sat
        # at :10696).
        return (MERGED, "") if _names_this_pr(m.group(1), pr) else ("", "")
    m = MERGED_NO_SHA_RE.search(log)
    if m:
        return (MERGED, "") if _names_this_pr(m.group(1), pr) else ("", "")
    return "", ""


def _names_this_pr(found: str, pr: int) -> bool:
    """Does the number a success line carries name the PR being collected?"""
    return _int_or_none(found) == pr


def _repo_key(value: str) -> str:
    """`owner/name`, lower-cased, with any transport prefix and `.git` dropped.

    ⛔ BOTH SIDES OF THE REPO COMPARISON MUST BE IN THE SAME FORM. The rail
    NORMALISES its target before printing it (`rail_normalize_slug` drops an
    attestable host), so `github.com/o/r` is announced as `o/r` — while `launch`
    writes `REPO=` exactly as it was given. Comparing those raw made a REAL merge
    read `refused`, with a reason ("the rail did not merge (e.g. --dry-run)") that
    says the opposite of what happened. Same two fields, one form.

    ⛔ `.git` IS PART OF A CLONE URL'S SPELLING, NOT OF THE REPO'S NAME, and a clone
    URL is what an operator pastes. Keeping the suffix made `_slug` produce
    `rail-o-r.git-7.md` beside `rail-o-r-7.md` — a SECOND record and a SECOND
    `flock`, i.e. TWO LIVE RAILS ON ONE PR, the #3061 collision this module's lock
    exists to refuse. The same paste produces the SCP form
    (`git@github.com:o/r.git`), whose colon glues the host to the owner inside one
    `/`-segment, so that prefix is stripped here too — this function is the ONE
    canonicaliser, and a spelling it cannot fold is a spelling the lock cannot see.
    """
    raw = value.strip()
    # SCP-style clone URL (`git@github.com:o/r[.git]`): no `://`, and the colon is a
    # path separator rather than part of the slug. A `://` form needs nothing — its
    # scheme segment is dropped by the `[-2:]` slice below.
    if "://" not in raw and ":" in raw:
        raw = raw.rsplit(":", 1)[-1]
    parts = [p for p in raw.split("/") if p]
    if not parts:
        return ""
    name = parts[-1]
    if name.lower().endswith(".git"):
        name = name[:-4]
    if len(parts) < 2:
        return name.lower()
    return f"{parts[-2]}/{name}".lower()


def _names_this_repo(log: str, repo: str) -> bool:
    """Does the rail's own evidence line say it graded the repo this artifact
    names? ONE-SIDED: no evidence line, or no `REPO=` in the artifact, means there
    is nothing to contradict, so the answer is True; a line naming a different repo
    is False (no evidence). The LAST line wins — a rail prints one per resolution
    and every later call uses the final answer. Both sides are compared as
    `owner/name`, so a normalised announcement matches a raw artifact value."""
    found = REPO_EVIDENCE_RE.findall(log)
    if not found or not repo:
        return True
    return _repo_key(found[-1]) == _repo_key(repo)


# ── collect ──────────────────────────────────────────────────────────────────
@dataclass
class RailState:
    pr: int
    state: str
    # The repo the ARTIFACT names, not the one `collect` was called with: it is what
    # the rail's own evidence line has to agree with (`_names_this_repo`).
    repo: str = ""
    run_ids: list[str] = field(default_factory=list)
    since: str = ""
    head: str = ""
    pid: int = 0
    child: int = 0
    exit_code: int | None = None
    merge_sha: str = ""
    reason: str = ""
    artifact: str = ""
    log: str = ""

    @property
    def run_id(self) -> str:
        return self.run_ids[-1] if self.run_ids else "-"

    def line(self) -> str:
        return f"RAIL(#{self.pr}, {self.state}, {self.run_id}, {self.since or '-'})"

    def as_dict(self) -> dict:
        return {
            "pr": self.pr,
            "state": self.state,
            "run_ids": self.run_ids,
            "run_id": self.run_id,
            "since": self.since,
            "head": self.head,
            "pid": self.pid,
            "child": self.child,
            "exit_code": self.exit_code,
            "merge_sha": self.merge_sha,
            "reason": self.reason,
            "artifact": self.artifact,
            "log": self.log,
        }


@dataclass
class _Snapshot:
    """ONE read of the artifact, plus the attempt-scoped tail of its log.

    ⛔ EVERY VALUE A VERDICT READS COMES FROM ONE SNAPSHOT. It replaces the loose
    `text` / `log` / `exits` triple, which let a re-read update `text` while `log`
    and `exits` still described the PRE-probe artifact — so a merge line the rail
    had already written was invisible to `_merge_evidence` and the rail read
    `lost`. "Read after the probe" has to be a property of the whole verdict, not
    of one field in it.
    """
    text: str
    log: str
    logp: str
    exits: list[str]
    pid: int
    child: int


def _attempt_log(full: str, attempt: str) -> str:
    """The tail of `full` that belongs to `attempt`, or "" when no mark can be
    attributed to it.

    ⛔ THE MARK IS THE EVIDENCE BOUNDARY, SO IT HAS TO NAME ITS ATTEMPT. The
    boundary used to be "the last mark in the file", which has a window: `launch`
    truncates the artifact and stamps the new `ATTEMPT_ID=` BEFORE its mark lands,
    so a `collect` in that instant took the PREVIOUS attempt's tail as this
    attempt's — and a previous `✅ merged` line then read as a merge this attempt
    never announced. An id-carrying mark bounds only its own attempt.

    COMPATIBILITY IS ONE-SIDED ON PURPOSE, mirroring `_attempt_exits`: a log whose
    marks carry NO id at all predates the key, so it keeps its old whole-tail
    meaning. As soon as ANY mark carries an id, a mark that is not ours bounds
    nothing — that is the fail-CLOSED direction (empty evidence ⇒ refused/lost,
    never a merge).
    """
    cut_mine: int | None = None
    cut_legacy: int | None = None
    cut_any: int | None = None
    any_id_mark = False
    pos = 0
    for line in full.splitlines(keepends=True):
        if line.lstrip().startswith(ATTEMPT_MARK):
            cut_any = pos
            m = ATTEMPT_MARK_ID_RE.search(line)
            if m:
                any_id_mark = True
                if attempt and m.group(1) == attempt:
                    cut_mine = pos
            else:
                cut_legacy = pos
        pos += len(line)
    if not attempt:
        return full[cut_any:] if cut_any is not None else ""
    if cut_mine is not None:
        return full[cut_mine:]
    if not any_id_mark and cut_legacy is not None:
        return full[cut_legacy:]
    return ""


def _read_snapshot(art: Path, logp_default: Path,
                   text: str | None = None) -> _Snapshot:
    """Read the artifact (unless the caller hands back the exact text it just
    compared) and derive every field a verdict reads from THAT one read."""
    if text is None:
        text = art.read_text(encoding="utf-8", errors="replace")
    logp = Path(read_field(text, "LOG", str(logp_default)))
    log = ""
    if logp.exists():
        full = logp.read_text(encoding="utf-8", errors="replace")
        # ⛔ THE LOG IS APPEND-ONLY ACROSS ATTEMPTS. Parsing the whole file lets a
        # DEAD attempt inherit a PREVIOUS attempt's `✅ merged` line and be
        # reported merged — evidence forged by concatenation. Every verdict read
        # is scoped to THIS attempt, by the id its own mark carries.
        # ⛔ NO MARK FOR THIS ATTEMPT ⇒ NO SCOPED EVIDENCE. Our launcher always
        # writes the mark before it spawns, so a log with no mark attributable to
        # this attempt is not this attempt's: reading it whole would let a dead
        # attempt inherit ANY earlier `✅ merged` line — the forged-evidence shape
        # the scoping exists to prevent. Fail CLOSED (empty evidence ⇒
        # refused/lost), never "read everything".
        log = _attempt_log(full, read_field(text, "ATTEMPT_ID"))
    return _Snapshot(
        text=text,
        log=log,
        logp=str(logp),
        exits=_attempt_exits(text),
        pid=_int_or_none(read_field(text, "PID", "0")) or 0,
        child=_int_or_none(read_field(text, "CHILD_PID", "0")) or 0,
    )


def _state_from(snap: _Snapshot, pr: int, art: Path) -> RailState:
    """The `waiting` skeleton a verdict is filled into."""
    return RailState(
        pr=pr,
        state=WAITING,
        repo=read_field(snap.text, "REPO"),
        run_ids=sorted(set(RUN_ID_RE.findall(snap.log)), key=lambda s: int(s)),
        since=read_field(snap.text, "STARTED_AT"),
        head=read_field(snap.text, "HEAD"),
        pid=snap.pid,
        child=snap.child,
        artifact=str(art),
        log=snap.logp,
    )


def _identity(snap: _Snapshot) -> tuple[str, int, int]:
    """Which ATTEMPT, and which processes, a snapshot describes.

    The loop compares this before and after a probe so a conclusion is never drawn
    from an artifact a `start` replaced underneath it: `launch` truncates and
    rewrites the whole record (new `ATTEMPT_ID`, new `PID`), so identity — not the
    field values read separately — is what says "this is still the record I probed".
    """
    return (read_field(snap.text, "ATTEMPT_ID"), snap.pid, snap.child)


def _exit_verdict(st: RailState, snap: _Snapshot) -> RailState:
    """The verdict for an artifact that carries this attempt's `EXIT=`."""
    code = _int_or_none(snap.exits[-1])
    st.exit_code = code
    merged, sha = _merge_evidence(snap.log, st.pr, st.repo)
    if merged == MERGED:
        st.state, st.merge_sha = MERGED, sha
        if not sha:
            st.reason = ("merged; the rail could not read the merge commit "
                         "(admin-merge.sh deliberately does not print it)")
        return st
    # Exit 0 with no merge line: the rail DECLINED to merge (a --dry-run,
    # a --print-bounds, a help exit) or refused. Either way there is no
    # merge, and saying `merged` would release the next beat's gate on
    # evidence that does not exist.
    st.state = REFUSED
    hits = REFUSAL_RE.findall(snap.log)
    st.reason = (hits[-1].strip()[:400] if hits else
                 (f"exited {code} without a merge line — the rail did not "
                  "merge (e.g. --dry-run), so this is not a merge"
                  if code == 0 else "no refusal line in the log"))
    return st


def collect(state_dir: Path, repo: str, pr: int) -> RailState | None:
    """Read the artifact and the rail's log. NO side effects: no writes, no
    network, no process signals. The only external call is a local `ps` state
    read, and only on a box without procfs. Returns None when no artifact
    exists.

    ⛔ AND IT ANSWERS `absent`, IT DOES NOT TRACEBACK, WHEN THE RECORD IS UNLINKED
    UNDER IT. `rm <artifact>` is the recovery BOTH verbs print, `launch` replaces
    the record wholesale, and this function reads it several times (once before the
    probe, once after). A `FileNotFoundError` escaping here exits 1 — the code this
    tool publishes as `--assert-merged` "the state is NOT merged" — so a crash wore
    the gate's exit code, which is the one signal a fleet beat must be able to
    trust. The honest answer for a record that no longer exists is `absent` (3).
    """
    try:
        return _collect_read(state_dir, repo, pr)
    except FileNotFoundError:
        return None


def _artifact_for_any_spelling(state_dir: Path, repo: str, pr: int) -> Path | None:
    """This PR's artifact, whether it was written under `repo`'s CURRENT name or
    an OLDER one. `None` when no record for `pr` names this repo.

    ⛔ CANONICALISING `_slug` RENAMED EVERY RECORD THAT ALREADY EXISTED, AND A
    RENAME THE GUARD CANNOT SEE IS THE GUARD BEING BYPASSED. Before this module
    keyed on `_repo_key`, `_slug` was `repo.replace("/", "-")` on the RAW string,
    so a record written then lives at `rail-github.com-o-r-7.md`. Reading only
    `artifact_path(...)` would miss it — and the miss is not cosmetic, it is BOTH
    halves of the #3061 collision this module exists to refuse:

      * `launch` calls `collect` for its duplicate-rail guard, so a miss means a
        SECOND rail on one PR (`collect` answers `absent`);
      * `collect --assert-merged` — the fleet's MACHINE GATE — would answer 3/1
        about a rail whose record is sitting on disk with a LIVE pid in it.

    The trigger is a version boundary, not an exotic spelling: the state dir is
    shared and long-lived, rail processes legitimately outlive a code update, and
    a peer lane can still be running an older copy of this tool (farm drift is
    routine — see AGENTS.md). So the read is spelling-INSENSITIVE even though
    every new write is canonical.

    ⛔ AND THE SCAN IS ANCHORED ON `REPO=`, NOT ON THE FILENAME. Matching the file
    name would re-derive the identity from the thing that was wrong; the record
    already carries the repo it rails, and `_repo_key` is the module's ONE way to
    decide two spellings are one repo. A candidate whose `REPO=` cannot be read,
    or names a different repo, is not this PR's record and is skipped.
    """
    art = artifact_path(state_dir, repo, pr)
    if art.exists():
        return art
    key = _repo_key(repo or "")
    sd = Path(state_dir)
    if not key or not sd.exists():
        return None
    for cand in sorted(sd.glob(f"rail-*-{pr}.md")):
        try:
            text = cand.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _repo_key(read_field(text, "REPO") or "") == key:
            return cand
    return None


def _collect_read(state_dir: Path, repo: str, pr: int) -> RailState | None:
    """The read itself (see `collect` for the contract, the two passes and the
    attempt-scoping rationale)."""
    art = _artifact_for_any_spelling(state_dir, repo, pr)
    if art is None:
        return None
    # ⛔ DERIVED FROM THE RECORD, NOT RE-SLUGGED FROM `repo`: a record written under
    # an older spelling has its log under that spelling too (`rail-<slug>-<pr>.log`
    # is the artifact's path with `.md` → `.log`), so re-slugging would pair a legacy
    # record with a canonical log that does not exist — the rail's own output
    # unreadable, which reads as `lost`/`refused` with no reason.
    logp_default = art.with_suffix(".log")
    # ⛔ TWO PASSES, AND THE SECOND IS NOT A RETRY OF A FAILURE — it closes a
    # STALE READ. The artifact is read BEFORE the liveness probe, so a rail that
    # writes `EXIT=` and exits *while* that probe runs is seen as GONE holding a
    # read that predates its EXIT: `merged` with `exit_code=None` and the false
    # reason "the EXIT line was never written", for a rail that wrote one. The
    # state was still `merged` (the rail's own success line was in the log) so no
    # gate moved, but the exit code and the explanation were both wrong — and it
    # is not theoretical: it made `test_merged_state_names_the_sha_the_rail_printed`
    # flake 2/2 at load 27 on 2026-11-03 while passing in isolation.
    #
    # So the "gone" conclusion is only drawn from a SNAPSHOT taken after the
    # probe. One extra read, and only on the path where we are about to declare a
    # rail dead.
    #
    # ⛔ AND THE RE-READ IS ADOPTED, NOT JUST COMPARED. Two passes narrow the race
    # window but cannot close it if the value the loop used to DETECT the change is
    # not the value the verdict is drawn from: when the artifact moved on the LAST
    # pass the loop could not take another pass, so every conclusion below read the
    # PRE-probe snapshot instead — a rail that wrote `✅ merged …` and then `EXIT=`
    # in the second window was reported `lost`, the same defect one pass over, and
    # the one direction the module must never answer (a real merge scoped out, and
    # `start` released on a merged PR — #2235, #3061). The adoption below makes the
    # final snapshot a post-probe read in EVERY exit from the loop, including the
    # ones that conclude.
    snap = _read_snapshot(art, logp_default)
    probes = 2
    # ⛔ WHAT THE PROBES ACTUALLY EXAMINED. `launch` REPLACES the artifact wholesale
    # on every attempt, so a `start` that lands inside this collect's probe window
    # hands the loop a record (new `ATTEMPT_ID`, new `PID`) that no probe here has
    # ever looked at. Publishing a conclusion drawn from it is how a LIVE rail gets
    # reported `lost` while the verdict prints that very pid — the class this module
    # forbids (`lost` is the state `start` accepts, #3061). The identity is recorded
    # per probe and re-checked before any conclusion.
    probed: tuple[str, int, int] | None = None
    while True:
        st = _state_from(snap, pr, art)
        if snap.exits:
            return _exit_verdict(st, snap)

        # No EXIT line yet. Alive ⇒ waiting; gone ⇒ lost (NEVER merged). ⛔ ALIVE
        # IS "THE WRAPPER **OR** THE RAIL": a killed wrapper leaves the rail
        # running, and reading that as `lost` was the P1 (module docstring).
        if probes > 0:
            probed = _identity(snap)
            if _rail_alive(snap.pid, snap.child, str(art),
                           age=_age_seconds(st.since)):
                st.state = WAITING
                return st
        if probes == 0:
            # Both probes said gone and the adopted snapshot is the post-probe
            # read: conclude from it. A third probe would only re-open the same
            # window, and an artifact that keeps moving is a broken rail — the
            # verdict below still reads the FRESHEST read taken after a probe.
            break
        probes -= 1

        # Everything below concludes from the ABSENCE of `EXIT=`, so it needs a
        # read taken after the probe that just said the writer is gone. Loop on the
        # fresh text when the artifact has moved; break when it has not (including
        # `CHILD_STARTED=`, which the child appends and which changes nothing a
        # verdict reads).
        fresh = art.read_text(encoding="utf-8", errors="replace")
        # ⛔ EVERY FIELD A PROBE READS IS PART OF THE "DID IT MOVE" TEST, or an
        # adoption can hand the verdict a value no probe ever saw. `PID=`/`SID=` are
        # appended by `launch` AFTER `Popen` returns — a SEPARATE write from the
        # `SPAWNING=1` one — so a `collect` that lands between them reads "no pid".
        # If that arrives during the probe and is adopted WITHOUT re-probing, the
        # post-loop arms (keyed on `pid == 0`) skip the `SPAWNING` protection and
        # conclude `lost` for a live wrapper: `start` accepts `lost`, so a second
        # rail goes onto a PR that is still merging (#3061) and the truncation
        # scopes its `✅ merged` out (#2235). So a newly-arrived pid forces another
        # pass, exactly as a newly-arrived `CHILD_PID=` does.
        moved = (_attempt_exits(fresh) != snap.exits
                 or (not snap.pid and bool(read_field(fresh, "PID")))
                 or (not snap.child and bool(read_field(fresh, "CHILD_PID")))
                 # A REPLACED record is a move: `launch` rewrites the artifact
                 # wholesale, so a new `ATTEMPT_ID` (or a pid that CHANGED, not
                 # merely arrived) is a different attempt whose carrier no probe
                 # here has examined.
                 or _identity(_read_snapshot(art, logp_default, text=fresh))
                 != _identity(snap))
        # ⛔ ADOPT THE FRESH SNAPSHOT ON EVERY EXIT FROM THE LOOP — INCLUDING THE
        # `break` — because `_read_snapshot` is what re-reads the LOG, and the log
        # is where every word a verdict reads is written. The artifact does not move
        # while a rail WAITS (it changes only at `PID=`/`CHILD_PID=`/
        # `CHILD_STARTED=` and at `EXIT=`), so an artifact-only "did it move" test
        # made this `break` conclude from a log read taken BEFORE the probe that had
        # just said the writer was gone: a rail that printed `✅ merged …` and then
        # died inside that window was reported `lost` — a few lines below the
        # comment saying that cannot happen, and `lost` is the state `start` ACCEPTS
        # (so the next beat truncates the log and the announcement is gone for good:
        # #2235, #3061).
        snap = _read_snapshot(art, logp_default, text=fresh)
        if not moved:
            break
    # ⛔ THE SKELETON IS RE-DERIVED FROM THE ADOPTED SNAPSHOT. `st` was built at the
    # TOP of the last iteration, from the snapshot the loop then replaced — so
    # `since` (the `SPAWNING_TTL_S` age input), `pid`, `head` and `run_ids` would
    # describe a read the verdict no longer uses. A re-launch that rewrites the
    # artifact during the probe would be aged against the PREVIOUS attempt's
    # `STARTED_AT` and read `lost`, with a reason claiming a launch that just
    # started never spawned.
    st = _state_from(snap, pr, art)
    if probed is not None and _identity(snap) != probed:
        # ⛔ THE ONLY HONEST ANSWER FOR A RECORD THE PROBES NEVER SAW. Concluding
        # from it would publish a verdict about one attempt using another's
        # evidence — `lost` for a LIVE replacement, or `merged` for a merge this
        # attempt did not make. `waiting` is the fail-CLOSED answer: `start` refuses
        # a `waiting` claim, so nothing is truncated, and the reason tells the
        # caller exactly what to do (one more collect, which probes the new record).
        st.state = WAITING
        st.reason = ("the artifact was REPLACED while this collect was probing — a "
                     "new attempt owns it now and no probe here examined it; "
                     "re-run `collect` to read that attempt's own verdict")
        return st
    text, log = snap.text, snap.log
    pid, child = snap.pid, snap.child
    # ⛔ `not exits` USED TO BE SPELLED HERE and could never be false — the `if
    # snap.exits: return` at the top of the loop has already returned — so it
    # described a condition this branch cannot reach. Dropped rather than kept:
    # a dead clause is a second, wrong description of the guard.
    if pid == 0 and read_field(text, "SPAWNING"):
        # Between `SPAWNING=1` and the `PID=` append the rail IS alive but
        # unnamed. Reporting `lost` there defeats the same guard that refuses a
        # second rail (#observed live 2026-11-03).
        #
        # ⛔ BUT THE SENTINEL MUST EXPIRE. A launch killed in exactly this window
        # (or a `Popen` that raised, e.g. EAGAIN under cell load) leaves
        # `SPAWNING=1` with no pid and nothing that would ever write anything
        # else — so an unexpired sentinel would report a live rail that does not
        # exist FOREVER, and the duplicate guard would refuse every later start:
        # the PR becomes unrailable. Past the TTL that state is `lost`, which is
        # both true (no rail is running) and recoverable (a new start is allowed).
        #
        # ⛔ `pid == 0` IS NOT "NO RAIL" EITHER: the artifact may NAME one. `launch`
        # appends `PID=` only after `Popen` returns, so a launcher killed in that
        # instant leaves `SPAWNING=1` with no wrapper pid while the rail it already
        # spawned has written `CHILD_PID=` and is merging. Concluding `lost` there
        # is the same durable-wrong-verdict shape as the fork window: the next
        # `start` truncates the artifact and scopes the rail's real `✅ merged` out
        # for good. So the evidence and the named rail are read BEFORE the sentinel
        # is allowed to mean "nothing started".
        #
        # ⛔ THIS ARM HAS NO AGE BOUND — AND THE COMMENT HERE ONCE CLAIMED ONE, then
        # (after that was corrected) claimed none was POSSIBLE. Both were wrong, in
        # opposite directions, so this states the actual mechanism.
        #
        # `_pid_alive(child, "")` is a liveness-only probe, and the residual is real
        # and documented in `_pid_alive`: a RECYCLED pid reads `waiting`. But the
        # artifact DOES carry the material to rule that out — `CHILD_STARTED=`, the
        # wrapper's clock immediately AFTER the fork, so the rail's own start time is
        # <= it. A process cannot have started before it was forked, so a pid whose
        # start time is LATER than that stamp is provably NOT this rail.
        #
        # ⛔ WHY IDENTITY AND NOT A TTL: a rail's wait is set by the runner QUEUE and
        # is unbounded (the module docstring's measured case is >= 6660 s, against a
        # `SPAWNING_TTL_S` of 60). Any AGE bound here would expire the claim of a
        # GENUINELY LIVE RAIL, and `lost` is exactly the state `start` accepts — so a
        # TTL trades a fail-CLOSED wedge for a SECOND RAIL on a PR that is still
        # merging (the #3061 collision, and the same durable-wrong-verdict shape as
        # the fork window this arm exists to close).
        # `test_a_named_rail_under_spawning_is_not_no_rail` pins that: its artifact
        # carries a STARTED_AT an hour old and NO `CHILD_STARTED`, and a live named
        # rail must still read `waiting` there — which is exactly what the identity
        # test falls back to when the stamp is absent (unmeasurable ⇒ keep the claim).
        #
        # The test is ONE-SIDED on purpose (`_pid_is_a_stranger`): it can only ever
        # conclude "not our rail", never "ours", so it cannot produce a false `lost`
        # and it never expires a live rail however long the queue takes. Fails CLOSED.
        #
        # ⛔ ALIVE OUTRANKS ANNOUNCED, SO THE CARRIER IS CHECKED FIRST. The `STATES`
        # table says exactly that (an announcement beats `lost`, never `waiting`),
        # and the order here used to say the opposite: a rail that had printed its
        # success line and was still doing its post-merge bookkeeping read `merged`,
        # which made `--assert-merged` exit 0 for an in-flight merge and released
        # `start`'s duplicate guard (it refuses only `waiting`). The merge is not
        # lost by waiting — the log keeps the line, and the verdict becomes `merged`
        # when the carrier exits.
        #
        # ⛔ BUT ANNOUNCED STILL OUTRANKS **`lost`**, AND A RECYCLED PID IS `lost`.
        # Ordering the carrier first must not run the stranger branch past the merge
        # evidence: a stranger carries nothing, while the announcement was printed
        # only after the API confirmed `state=MERGED` — so the merge IS the verdict
        # there. Skipping that check reported `lost` for a merged PR, which `start`
        # accepts and the truncation then makes permanent (#2235, #3061). So: a live
        # carrier that is OURS ⇒ waiting; an announcement ⇒ merged; a live STRANGER
        # ⇒ lost (nothing of ours is left).
        alive_carrier = child > 0 and _pid_alive(child, "")
        crowd = False
        if alive_carrier:
            child_started = read_field(text, "CHILD_STARTED")
            child_ticks = read_field(text, "CHILD_START_TICKS")
            crowd = _pid_is_a_stranger(child, child_started, child_ticks)
            if not crowd:
                st.state = WAITING
                st.reason = ("a rail named itself (CHILD_PID) but its wrapper never "
                             "recorded a pid — waiting for the rail's own record")
                if _age_seconds(child_started) is None:
                    # No identity material, so the recycled-pid residual DOES apply
                    # here: name it and name the way out, rather than pretending a
                    # bound exists.
                    st.reason += ("; this artifact records no CHILD_STARTED, so that "
                                  "pid cannot be identity-checked and a RECYCLED one "
                                  "would lift only when it exits — clear with "
                                  f"`rm {st.artifact}`")
                return st
        merged, sha = _merge_evidence(log, pr, st.repo)
        if merged == MERGED:
            st.state, st.merge_sha = MERGED, sha
            st.reason = ("the rail's log records the merge"
                         + (" (no merge sha printed)" if not sha else "")
                         + "; the EXIT line was never written")
            return st
        if crowd:
            st.state = LOST
            st.reason = (f"the pid the rail named (CHILD_PID={child}) started "
                         "AFTER this rail was forked, so it is a DIFFERENT "
                         "process — the rail is gone and its pid was RECYCLED, "
                         "not decided")
            return st
        age = _age_seconds(st.since)
        if child <= 0 and age is not None and age <= SPAWNING_TTL_S:
            st.state = WAITING
            st.reason = "launching (no pid recorded yet)"
            return st
        st.state = LOST
        st.reason = ("the launch died between the artifact write and the spawn — "
                     "no rail ever started (SPAWNING never became a pid)"
                     if child <= 0 else
                     "the launch died before recording a wrapper pid, and the rail "
                     "it named (CHILD_PID) is gone too — killed, not decided")
        return st
    # A merge the rail ANNOUNCED and then died before recording the exit code is
    # still a merge — the announcement is made only after the API confirmed
    # `state=MERGED`. Reading it is not a gate being relaxed: the rail's gates ran
    # to completion, only the bookkeeping line was lost. Anything short of that
    # announcement stays `lost` (a vanished rail has NO verdict — #2235).
    merged, sha = _merge_evidence(log, pr, st.repo)
    if merged == MERGED:
        st.state, st.merge_sha = MERGED, sha
        st.reason = ("the rail's log records the merge"
                     + (" (no merge sha printed)" if not sha else "")
                     + "; the EXIT line was never written")
        return st
    st.state = LOST
    st.reason = "the rail process is gone with no EXIT line — killed, not decided"
    return st


# ── start ────────────────────────────────────────────────────────────────────
@dataclass
class Launch:
    pr: int
    pid: int
    sid: int
    artifact: str
    log: str
    head: str


class Refused(Exception):
    """A precondition the launcher itself enforces (never a rail verdict)."""


def _gh(argv: list[str], cwd: Path,
        timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run `gh`; a MISSING BINARY is a failed query, never a crash.

    Both callers below are best-effort (`_head_sha` already answers `unreadable`
    on a non-zero exit, `_resolve_repo` refuses), but an absent `gh` raised
    `FileNotFoundError` OUT of `launch()` — so the launcher died instead of
    stamping its artifact, and every arm of `tests/test_rail_detach.py` read as
    a failure on the #9018 fallback pool, whose `ubuntu-latest` is a bare runner
    with a minimal non-login PATH and no `gh` (measured 2026-11-03: 20 red arms
    on `pi-runner-vm-2`, `gh: No such file or directory`, on a PR whose diff
    touches neither this tool nor its test). rc=127 is what a shell reports for
    a missing command, so the callers' `returncode != 0` arms handle it — the
    same shape `tools/who_is_on.py` already uses for `git`
    (`except FileNotFoundError: return 127, "", "<bin>: command not found"`).

    `timeout` is threaded through for the callers that must stay BOUNDED (the
    merge of #10691's `GH_PROBE_TIMEOUT_S` bound onto main's helper): the bound
    and the absent-binary answer are two halves of one call, so a caller that
    needs the bound must not fall back to a raw `subprocess.run` and lose the
    127.

    ⛔ A SECOND, INDEPENDENT MEASUREMENT OF THE SAME DEFECT, kept from the other
    side of this merge (#10622): `test (h)` on `pi-runner-vm-6`/Linux-ARM64, run
    41732583553, killed all 20 cases in `tests/test_rail_detach.py` at this
    call. Two pool boxes, one root — the missing binary, not a rail defect.

    The `-> subprocess.CompletedProcess` annotation above is a CONTRACT, not a
    hint: this function NEVER returns `None` (the `except` arm fabricates a
    process with rc=127), so neither caller may carry an `out is not None`
    guard. Two of them did for a while — inherited from the branch side of the
    #10622 merge, where `_gh` was allowed to return `None` — and they were
    unreachable branches contradicting their own annotation. Dropped rather
    than kept: a guard that cannot fire is not defence in depth, it is a
    second, wrong description of what this function returns.
    """
    try:
        return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(argv, 127, "",
                                           "gh: command not found")


def _resolve_repo(cwd: Path) -> str:
    env_repo = os.environ.get("GH_REPO", "")
    try:
        out = _gh(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            Path(cwd),
            timeout=GH_PROBE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise Refused(
            f"`gh repo view` did not answer within {GH_PROBE_TIMEOUT_S}s — refusing to "
            "detach a rail at a target this launch could not name"
        ) from None
    except (OSError, subprocess.SubprocessError) as e:
        # ⛔ `gh` ABSENT is not a traceback. This call is on `start`'s own path, so
        # an uncaught `FileNotFoundError` escaped `main()` and exited 1 — the code
        # this tool reserves for `collect --assert-merged` "not merged" — while
        # the docstring promises "2 = environment error" (a minimal-PATH runner
        # has already produced `exit code 127` on `gh` elsewhere in this repo,
        # #9018). `_ps` two functions away already treats an unrunnable probe as a
        # distinct answer; this does the same.
        raise Refused(
            f"could not run `gh` to name the repo ({type(e).__name__}: {e}) — refusing "
            "to detach a rail at a target this launch could not name"
        ) from None
    if out.returncode == 0 and out.stdout.strip():
        repo = out.stdout.strip()
        if env_repo and env_repo != repo:
            # Mirrors the rail's own #2235 refusal: an inherited GH_REPO must
            # never silently retarget the merge away from this checkout.
            raise Refused(
                f"GH_REPO={env_repo} disagrees with this checkout ({repo}) — "
                "unset GH_REPO rather than detaching a rail at an unknown target"
            )
        return repo
    detail = (out.stderr or "").strip().splitlines()
    # ⛔ BOTH SIDES' WORDING (#10622 merge): this branch's test asserts the message
    # says WHAT to fix ("installed", `test_a_missing_gh_is_a_REFUSAL_not_a_traceback`),
    # and main's carries the stderr detail plus the two remedies. Taking either side
    # alone fails the other's assertion or drops the operator's remedy.
    raise Refused(
        "could not resolve the repo from this checkout (`gh repo view` failed"
        + (f": {detail[-1]}" if detail else "")
        + ") — is `gh` installed and authenticated? pass `--repo owner/name` to name "
        "the artifact yourself, or install the `gh` CLI"
    )


def _head_sha(cwd: Path, repo: str, pr: int) -> str:
    """The head sha for the artifact, or `unreadable`. INFORMATIONAL ONLY —
    nothing gates on it — and BOUNDED, because this call is made while the
    duplicate-rail flock is held (see GH_PROBE_TIMEOUT_S)."""
    try:
        out = _gh(
            ["gh", "pr", "view", str(pr), "--repo", repo, "--json", "headRefOid",
             "-q", ".headRefOid"],
            Path(cwd),
            timeout=GH_PROBE_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
        # Same reason as `_resolve_repo`: the declared contract is "the sha *or*
        # `unreadable`", so a `gh` that cannot run has to answer `unreadable`
        # rather than escape. `HEAD` gates nothing.
        return "unreadable"
    return out.stdout.strip() if out.returncode == 0 else "unreadable"


def _shquote(value: object) -> str:
    """POSIX single-quote `value` for the child's `bash -c` body."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def child_script(rail: Path, repo: str, pr: int, logp: Path, art: Path,
                 extra: list[str], attempt: str = "") -> list[str]:
    """The detached rail's argv. NOTHING but the one lander is invoked: no gh
    merge, no flags added, no gate relaxed. `extra` is passed through verbatim
    because it is the operator's own rail invocation (e.g. `--dry-run`).

    The rail runs as a BACKGROUND job so this wrapper can `wait` for its exit
    code and record `EXIT=` — and it records its OWN pid (`$$`) before `exec`-ing
    the lander, so a wrapper killed at any point after the fork still leaves a name
    for a rail that is running (`$!` would be written BY the wrapper, i.e. lost in
    exactly the case this exists for). `exec` keeps the pid, so `CHILD_PID=` names
    the real rail.

    `attempt` is the id stamped into the artifact that this wrapper belongs to. It
    is written IMMEDIATELY BEFORE `EXIT=` so `collect` can tell this attempt's exit
    line from a previous attempt's late append (`_attempt_exits`) — the artifact
    is truncated by `launch` with no ordering against the old wrapper."""
    rail_args = " ".join(
        _shquote(a) for a in [str(rail), str(pr), "--repo", repo, *extra])
    # `bash -c <script> rail <art> <rail-argv...>`: $1 is the artifact, `$@` after
    # the shift is the lander invocation. Double quotes only — this string is
    # itself single-quoted by `_shquote`.
    self_naming_rail = (
        'printf "CHILD_PID=%s\\n" "$$" >> "$1"; shift; exec bash "$@"'
    )
    body = (
        f"bash -c {_shquote(self_naming_rail)} rail {_shquote(str(art))} {rail_args} "
        f">> {_shquote(logp)} 2>&1 &\n"
        f"child=$!\n"
        f"printf 'CHILD_STARTED=%s\\n' \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" >> {_shquote(art)}\n"
        # ⛔ A STEP-IMMUNE IDENTITY ANCHOR, WRITTEN FROM THE RAIL'S OWN /proc entry.
        # `CHILD_STARTED` is a WALL timestamp, and `btime` moves with wall-clock
        # STEPS, so comparing wall epochs makes a FORWARD step look like a pid
        # recycle — a live rail reads `lost` and `start` then accepts a second rail
        # (#3061). Field 22 (starttime, in clock ticks since boot) is the kernel's
        # own monotonic record of when THIS pid started, and it never moves: two
        # probes of one process always agree, and a recycled pid always differs. So
        # where procfs answers, identity is EXACT and needs no slack. Empty where
        # there is no procfs (macOS) — the reader then falls back to the wall stamp.
        # ⛔ PARSED AFTER THE LAST `) `, like the reader (`_proc_start_ticks`), NOT
        # positionally over the whole line. The comm field is parenthesised and may
        # itself contain spaces AND parens, so a positional split shifts every later
        # field — measured on a process whose comm is `a b)c`: `awk '{print $22}'`
        # returns **0** where the reader returns the real starttime. A tick count that
        # disagrees with the reader makes `_pid_is_a_stranger` conclude "not our rail"
        # for a rail that IS ours, which reads `lost` and lets `start` put a SECOND
        # rail on a PR that is still merging (#3061) — the fail-danger direction this
        # anchor exists to close. `[)]` is escaped so the regex is an ERE in every awk.
        f"printf 'CHILD_START_TICKS=%s\\n' "
        f"\"$(awk '{{ sub(/^.*[)] /, \"\"); print $20 }}' /proc/$child/stat 2>/dev/null)\" "
        f">> {_shquote(art)}\n"
        f"wait \"$child\"; rc=$?\n"
        f"printf 'EXIT_ATTEMPT=%s\\nEXIT=%s\\nFINISHED_AT=%s\\n' "
        f"{_shquote(attempt)} \"$rc\" \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" >> {_shquote(art)}\n"
        f"exit \"$rc\"\n"
    )
    return ["bash", "-c", body]


def launch(pr: int, *, cwd: Path, state_dir: Path, repo: str | None = None,
           extra: list[str] | None = None) -> Launch:
    """Start the rail detached and return immediately."""
    extra = list(extra or [])
    rail = Path(cwd) / RAIL_REL
    if not rail.exists():
        raise Refused(
            f"{RAIL_REL} not found under {cwd} — materialise it with "
            "`uv run python tools/link_agent_infra_scripts.py` (a fresh worktree "
            "has no scripts/ symlink, #9475)"
        )
    for var in FORBIDDEN_ENV:
        if os.environ.get(var):
            raise Refused(
                f"{var} is set in this environment — a detached rail must not carry "
                "a bypass. Unset it and re-run: detachment changes who outlives whom, "
                "never what is checked"
            )
    repo = _repo_key(repo or _resolve_repo(Path(cwd)))
    # ⛔ CANONICALISED ONCE, AT THE TOP, because `repo` is the identity THREE
    # things key on: the artifact/lock path (via `_slug`), the `REPO=` line written
    # into it, and every `gh` call that reads the PR head. Writing the raw spelling
    # while the path is canonical is how the duplicate-rail guard was bypassed —
    # `o/r` and `github.com/o/r` produced two artifacts, two locks and two live
    # rails on one PR (#10691 review). One form, everywhere.
    # ⛔ RESOLVE, because the child runs with `cwd=<checkout>`: a relative
    # `--state-dir` would otherwise resolve the artifact/log against the CHECKOUT
    # while the launcher writes them against the lane's own cwd — splitting one
    # rail across two paths, where a real merge reads back as `lost` and a failed
    # redirect (which stops bash before the rail ever runs) still prints STARTED.
    cwd = Path(cwd).resolve()
    state_dir = Path(state_dir).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    art = artifact_path(state_dir, repo, pr)
    logp = log_path(state_dir, repo, pr)

    # ⛔ THE DUPLICATE-RAIL GUARD IS A LOCK, NOT A PRECONDITION. `collect` then
    # `Popen` is check-then-act: two lanes (or a lane and a fleet beat) both read
    # `no live rail` and both spawn, which is exactly the #3061 collision the
    # guard exists to refuse — and two live rails then share one artifact, so the
    # loser's verdict overwrites the winner's.
    lock = Path(str(art) + ".lock")
    with lock.open("a+") as lockfh:
        fcntl.flock(lockfh.fileno(), fcntl.LOCK_EX)
        state_dir.mkdir(parents=True, exist_ok=True)
        prior = collect(state_dir, repo, pr)
        if prior is not None and prior.state == WAITING:
            # ⛔ THE HINT IS PER-ARM, because `pid == 0` covers TWO waiting arms and
            # only ONE of them has no clock: an un-pid'd launch that has named NO
            # child expires on its own within `SPAWNING_TTL_S`, and telling an
            # operator to `rm` THAT artifact would delete the record of a launch
            # that is mid-fork — its rail's `CHILD_PID=`/`EXIT=`/`✅ merged` appends
            # would then go to an unlinked inode (bash keeps the fd), so a real
            # merge reads back unreadable forever AND a second `start` is allowed.
            # So: the read-only check plus a CONDITIONAL remedy for a pid'd claim, the
            # identity warning for a named child, and the expiry for a bare sentinel.
            # `ALREADY LIVE` leads every arm — that is the phrase lanes match on.
            # ⛔ A pid'd claim gets a hint because it is the ONE arm where a RECYCLED
            # pid never lifts: a genuine wrapper lifts it by exiting, but a stranger
            # holding that number answers forever. Saying nothing there is a refusal
            # that names no way out. The `ps -p` gate is what keeps the `rm` from
            # being advice to unlink a live rail's own record.
            # ⛔ A NAMED CHILD IS CHECKED FIRST, BECAUSE IT IS THE ONE THAT CARRIES
            # THE RAIL. `prior.pid` is the WRAPPER, and the state this module
            # documents as its P1 is exactly "wrapper killed, rail alive": there the
            # pid arm's `ps -p <wrapper>` returns an empty table, its own gate reads
            # "not this PR's rail", and the `rm` it offers would unlink a LIVE rail's
            # record — the harm the paragraph above cites as the reason to withhold a
            # hint at all. Ordered the other way, the pid arm shadowed this one.
            if prior.child > 0:
                hint = (f" — the rail named CHILD_PID={prior.child} and that process "
                        "answers, so no CLOCK lifts this claim: it lifts only when "
                        "whatever process holds that pid exits (a RECYCLED one is not "
                        f"your rail). Verify with `ps -p {prior.child}`; if it is not "
                        f"this PR's rail, clear it with: rm {prior.artifact}")
            elif prior.pid:
                hint = (f" — verify with `ps -p {prior.pid}`: a genuine wrapper lifts "
                        "this when it exits, and a RECYCLED pid never does; if that "
                        f"process is not this PR's rail, clear it with: rm {prior.artifact}")
            else:
                hint = (f" — this launch has recorded no pid and named no child, so it "
                        f"expires on its own within {SPAWNING_TTL_S}s; nothing to "
                        "remove")
            raise Refused(
                f"a rail is ALREADY LIVE for PR #{pr} (pid {prior.pid}, since "
                f"{prior.since}) — two rails on one PR is the #3061 collision; "
                f"collect it with: collect {pr}{hint}"
            )

        head = _head_sha(Path(cwd), repo, pr)
        # The ARTIFACT is a fresh record per attempt — `ATTEMPT_ID` is the same idea
        # for the EXIT line, because the truncation below has no ordering against the
        # PREVIOUS wrapper's late append. The `.log` is TRUNCATED with it (a fresh
        # attempt starts a fresh log, rather than paying every earlier attempt's bytes
        # on every `collect`), so nothing a verdict needs comes from a previous
        # attempt's output.
        # `SPAWNING=1` is written BEFORE the spawn, because the window between the
        # two is a rail that exists but has no pid yet — and `collect` must not
        # call that `lost` (nor let a second rail through). `ATTEMPT_ID` is the
        # same idea for the EXIT line: the truncation below has no ordering against
        # the PREVIOUS wrapper's late append, so each attempt's exit is tagged.
        attempt = f"{os.getpid()}-{time.time_ns()}"
        art.write_text(
            f"# rail artifact — {repo}#{pr}\n"
            f"PR={pr}\nREPO={repo}\nHEAD={head}\nSTARTED_AT={_iso_now()}\n"
            f"CWD={cwd}\nLOG={logp}\nSPAWNING=1\nATTEMPT_ID={attempt}\n"
            f"RAIL_ARGS={' '.join([str(pr), '--repo', repo, *extra])}\n",
            encoding="utf-8",
        )
        # ⛔ TRUNCATED, like the artifact, and the mark NAMES ITS ATTEMPT. The log
        # is otherwise append-only forever: every `collect` reads it whole, so a
        # PR railed a dozen times pays a dozen attempts' worth of bytes on every
        # verdict read, and the boundary between attempts is only inferable from
        # a mark that named nothing. The truncation is ordered inside the flock
        # with the artifact's own, so a `collect` that sees the new `ATTEMPT_ID=`
        # never sees the previous attempt's tail as its own (`_attempt_log`).
        with logp.open("w", encoding="utf-8") as fh:
            fh.write(f"\n{ATTEMPT_MARK} {_iso_now()} attempt={attempt} "
                     f"(detached) =====\n")

        argv = child_script(rail, repo, pr, logp, art, extra, attempt=attempt)
        with logp.open("a", encoding="utf-8") as logfh:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=logfh,
                stderr=subprocess.STDOUT,
                start_new_session=DETACH_NEW_SESSION,  # ← the whole mechanism
                close_fds=True,
            )
        sid = os.getsid(proc.pid) if hasattr(os, "getsid") else proc.pid
        with art.open("a", encoding="utf-8") as fh:
            fh.write(f"PID={proc.pid}\nSID={sid}\n")
    return Launch(pr=pr, pid=proc.pid, sid=sid, artifact=str(art), log=str(logp), head=head)


# ── CLI ──────────────────────────────────────────────────────────────────────
def _state_dir(a) -> Path:
    return Path(a.state_dir) if a.state_dir else DEFAULT_STATE_DIR


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="rail_detach.py",
        description="Start the merge rail detached from this lane, and collect its verdict later.",
    )
    sub = p.add_subparsers(dest="verb", required=True)

    s = sub.add_parser("start", help="detach scripts/admin-merge.sh <PR> into its own session")
    s.add_argument("pr", type=int)
    s.add_argument("--repo", default=None, help="owner/name (default: this checkout's origin)")
    s.add_argument("--cwd", default=None, help="checkout the rail runs in (default: $PWD)")
    s.add_argument("--state-dir", default=None)
    s.add_argument("--json", action="store_true")

    c = sub.add_parser("collect", help="report a rail's verdict (read-only)")
    c.add_argument("pr", type=int)
    c.add_argument("--repo", default=None)
    c.add_argument("--state-dir", default=None)
    c.add_argument("--json", action="store_true")
    c.add_argument("--assert-merged", action="store_true",
                   help="exit 1 unless the state is `merged` (for the next fleet beat)")

    # Everything after a standalone `--` is the operator's OWN rail invocation,
    # passed through verbatim (e.g. `start 12 -- --dry-run`). Split BEFORE argparse:
    # a REMAINDER positional silently swallows the launcher's own options.
    argv = list(sys.argv[1:] if argv is None else argv)
    rail_extra: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, rail_extra = argv[:i], argv[i + 1:]

    a = p.parse_args(argv)
    sd = _state_dir(a)

    if a.verb == "start":
        cwd = Path(a.cwd or os.getcwd()).resolve()
        try:
            ln = launch(a.pr, cwd=cwd, state_dir=sd, repo=a.repo, extra=rail_extra)
        except Refused as e:
            print(f"rail_detach: ✗ REFUSED — {e}", file=sys.stderr)
            return 4
        except OSError as e:
            # ⛔ NOT exit 1: 1 is this tool's `collect --assert-merged` "not
            # merged" — the fleet's MACHINE GATE (see EXIT CODES). A `Popen`
            # that raised (EAGAIN under cell load, ENOMEM, a vanished CWD) is an
            # ENVIRONMENT error, so reporting it through 1 would read as a rail
            # that ran and refused. Same class as the missing-`gh` fix below,
            # same reason. The `SPAWNING=1` this leaves behind expires on its own
            # via `SPAWNING_TTL_S`, so the PR is not wedged.
            print(f"rail_detach: ✗ {e}", file=sys.stderr)
            return 2
        if a.json:
            print(json.dumps(ln.__dict__))
        else:
            print(f"STARTED(#{ln.pr}, pid {ln.pid}, sid {ln.sid}, head {ln.head[:12]})")
            print(f"  artifact {ln.artifact}")
            print(f"  log      {ln.log}")
            print(f"  collect: uv run python tools/rail_detach.py collect {ln.pr}")
        return 0

    repo = a.repo
    if not repo:
        # ⛔ `collect` is the verb that must work WHEN THE NETWORK DOES NOT — the
        # artifact already names its repo, so resolve from disk first and only
        # fall back to `gh`. A status read must not depend on an API call.
        cands = sorted(Path(sd).glob(f"rail-*-{a.pr}.md")) if Path(sd).exists() else []
        if len(cands) == 1:
            repo = read_field(cands[0].read_text(encoding="utf-8", errors="replace"),
                              "REPO") or None
        elif not cands:
            # ⛔ ZERO CANDIDATES IS AN ANSWER, NOT A REASON TO ASK GITHUB. The
            # artifact filename is `<repo slug>-<pr>`, so an artifact this glob does
            # not match cannot exist for ANY repo — the PR has never been railed
            # here, and that is `absent` (3) whatever `gh` would have said. Asking
            # `gh` first made the answer depend on the network: with `gh` failing,
            # an empty state dir exited 2 (environment error) instead of 3. The
            # `>1` case still resolves, because two repos' artifacts for one PR
            # number genuinely does not name a target.
            print(f"RAIL(#{a.pr}, absent, -, -)")
            return 3
    if not repo:
        try:
            repo = _resolve_repo(Path(os.getcwd()))
        except Refused as e:
            print(f"rail_detach: ✗ {e}", file=sys.stderr)
            return 2
    st = collect(sd, repo, a.pr)
    if st is None:
        print(f"RAIL(#{a.pr}, absent, -, -)")
        return 3
    if a.json:
        print(json.dumps(st.as_dict()))
    else:
        print(st.line())
        if st.head:
            print(f"  head {st.head}")
        if st.merge_sha:
            print(f"  merge_sha {st.merge_sha}")
        if st.reason:
            print(f"  reason {st.reason}")
    if a.assert_merged and st.state != MERGED:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
