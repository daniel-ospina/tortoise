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
import errno
import fcntl
import json
import os
import re
import shutil
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


def _absent_document(pr: int, as_json: bool) -> str:
    """The ABSENT answer, in whichever shape the caller asked for.

    ⛔ A MACHINE SURFACE MUST ANSWER MACHINE-READABLY ON *EVERY* PATH. Both absent
    returns printed a bare `RAIL(...)` line even under `--json`, so a consumer doing
    `json.loads(stdout)` CRASHED on the answer a lane hits most often — "this PR was
    never railed here" — while the exit code (3) was already correct, which is
    precisely why a key-on-exit-code caller never saw it.

    ⛔ The values carry the SAME TYPES as `RailState.as_dict()`, not just the same
    keys: emitting `None` where a real status emits `""` (and `0` for `pid`/`child`)
    makes `int(doc["pid"])` raise for a consumer that learned the schema from the
    normal path — the same break the JSON fix exists to remove, moved one level down.

    Pinned by `tests/test_rail_detach.py::test_absent_collect_answers_json_when_json_was_asked_for`,
    which compares key sets AND per-key types against a real `RailState`.
    """
    if not as_json:
        return f"RAIL(#{pr}, absent, -, -)"
    return json.dumps({
        "pr": pr, "state": "absent",
        "run_ids": [], "run_id": "-", "since": "",
        "head": "", "pid": 0, "child": 0, "exit_code": None,
        "merge_sha": "", "reason": "", "artifact": "", "log": "",
    })


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
    # ⛔ `.is_file()`, NOT `.exists()`: a torn `LOG=` line (a header write cut at
    # `LOG=`) resolves to `Path(".")`, which EXISTS — and `read_text()` then
    # raises `IsADirectoryError` out of `collect`, so `collect --assert-merged`
    # exited 1 ("the state is NOT merged") for a crash, which is the one signal
    # the tool's own docstring says must never wear that code.
    if logp.is_file():
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


def _exit_verdict(st: RailState, snap: _Snapshot, repo: str = "") -> RailState:
    """The verdict for an artifact that carries this attempt's `EXIT=`."""
    code = _int_or_none(snap.exits[-1])
    st.exit_code = code
    merged, sha = _merge_evidence(snap.log, st.pr, st.repo or repo)
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


def collect(state_dir: Path, repo: str, pr: int,
            artifact: Path | None = None) -> RailState | None:
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
        return _collect_read(state_dir, repo, pr, artifact)
    except FileNotFoundError:
        return None


def _candidate_records(state_dir: Path, repo: str, pr: int) -> list[Path]:
    """Every record for `pr` that names THIS repo — canonical first, then legacy."""
    art = artifact_path(state_dir, repo, pr)
    key = _repo_key(repo or "")
    sd = Path(state_dir)
    cands = [art] if art.exists() and _record_names_repo(art, repo) else []
    if key and sd.exists():
        for cand in sorted(sd.glob(f"rail-*-{pr}.md")):
            if cand == art:
                continue
            try:
                text = cand.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _repo_key(read_field(text, "REPO") or "") == key:
                cands.append(cand)
    return cands


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

    ⛔ TWO RECORDS CAN COEXIST DURING THE ROLLOUT, SO THE CHOICE IS BY LIVENESS,
    NOT BY NAMING. Once an old copy of this tool has written its legacy-named
    record beside the canonical one, "prefer the canonical path and return" reads
    a DEAD canonical record `lost` while the legacy record names the rail that is
    still merging — the module's own P1 (a live rail reported `lost`), and the
    state `launch`'s guard accepts, i.e. a SECOND rail on one PR. So when there
    is more than one candidate, the first one whose pid/child still answers wins.
    Nothing dead answering: canonical if there is one, else the newest
    `STARTED_AT` (the last attempt), else the first.

    ⛔ THE COMPATIBILITY IS ONE-DIRECTIONAL — this is the residual, stated so no
    reader mistakes "spelling-INSENSITIVE" for "the collision is closed both
    ways". An OLD copy of this tool reads only `artifact_path()` with the RAW-repo
    slug, so the canonical record this code writes is invisible to it: during the
    rollout an old-copy `launch` can still start a second rail on a PR the new
    code is already railing, and an old-copy `collect --assert-merged` can answer
    3 about a new rail that is live. The lock does not close that either — a
    `flock` is keyed on the file, and the legacy and canonical locks are different
    files. Dual-writing the legacy alias was rejected: it would resurrect the
    second lock this change exists to remove, and a compatibility artefact that
    the pre-fix reader WRITES to is not something the new code can keep correct.
    The direction that can be closed without re-introducing the collision is
    closed here, and the other one ends when the fleet is past this commit.
    """
    art = artifact_path(state_dir, repo, pr)
    cands = _candidate_records(state_dir, repo, pr)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    # ⛔ TERMINAL EVIDENCE OUTRANKS LIVENESS, BECAUSE A MERGE IS MONOTONE. Ranking
    # by liveness alone (cycle-2 fix) regressed the other direction, measured: a
    # canonical record carrying this attempt's `✅ merged` was outranked by a STALE
    # legacy record whose pid read live through `_rail_alive`'s group fallback, so
    # `collect --assert-merged` answered 1 ("did NOT merge") about a merged PR and
    # the merge sha became unreachable — the durable-wrong-verdict class #2235
    # forbids, aimed at the fleet's machine gate. Nothing can un-merge a PR, while
    # `waiting`/`lost`/`refused` are all still in flight in the sense that matters
    # here. So: merged first, then live, then canonical, then newest.
    for cand in cands:
        if _record_is_merged(cand, pr, repo):
            return cand
    for cand in cands:
        if _record_is_live(cand):
            return cand
    if art in cands:
        return art
    # Nothing answers: the newest attempt is the one to report, and `STARTED_AT`
    # orders attempts. An unreadable/garbled timestamp sorts last rather than
    # crashing the read-only verb (`_int_or_none`'s contract).
    def _started(p: Path) -> str:
        try:
            return read_field(p.read_text(encoding="utf-8", errors="replace"),
                              "STARTED_AT") or ""
        except OSError:
            return ""

    return sorted(cands, key=_started, reverse=True)[0]


def _record_is_merged(art: Path, pr: int, repo: str = "") -> bool:
    """Does this record carry THIS attempt's terminal merge?

    ⛔ SCOPED TO THE ATTEMPT, with the same two rules the verdict path uses —
    `_attempt_log` for the evidence boundary and `_merge_evidence` for "the rail
    SAID merged about THIS PR and THIS repo". Read unscoped, the log is
    append-only across attempts, so one old `✅ merged` line would make every later
    record for the same PR read merged (evidence forged by concatenation).
    """
    try:
        text = art.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    logp = Path(read_field(text, "LOG", str(art.with_suffix(".log"))))
    if not logp.exists():
        return False
    try:
        full = logp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    scoped = _attempt_log(full, read_field(text, "ATTEMPT_ID"))
    # ⛔ THE REQUESTED REPO IS THE FALLBACK, because a TORN record names none. A
    # header write cut before its `REPO=` line (SIGKILL/ENOSPC mid-`write_text`) is
    # admitted by `_record_names_repo` on purpose — a torn record must still read —
    # but the record's EMPTY repo was then handed to `_merge_evidence`, whose repo
    # cross-check is one-sided (`_names_this_repo`: an empty repo has nothing to contradict), so the
    # log's own `admin-merge: repo <other>` line — the reason that check exists — was
    # skipped, and `collect --assert-merged` certified a merge for a repo that never
    # merged. A torn record of the SAME repo still reads: the log names it.
    state, _sha = _merge_evidence(scoped, pr, read_field(text, "REPO") or repo)
    return state == MERGED


def _waiting_record(state_dir: Path, repo: str, pr: int) -> RailState | None:
    """The record of this PR (under ANY spelling) that says a rail is WAITING.

    ⛔ `collect` ANSWERS A VERDICT, AND THE DUPLICATE-RAIL GUARD NEEDS A LIVENESS
    FACT. When a merged record coexists with a live one — the rollout state this
    module exists for — `collect` correctly returns the MERGED one, so a guard
    reading `collect(...).state == WAITING` saw "not live" and started a SECOND rail
    on the same PR, truncating the live record on the way (measured). So the guard
    asks each candidate, and refuses on the first one that is waiting.
    """
    for cand in _candidate_records(state_dir, repo, pr):
        st = collect(state_dir, repo, pr, artifact=cand)
        if st is not None and st.state == WAITING:
            return st
    return None


def _record_names_repo(art: Path, repo: str) -> bool:
    """Does this record's own `REPO=` name `repo` (or name nothing at all)?

    ⛔ THE CANONICAL PATH MUST BE ADMITTED BY `REPO=`, NOT BY ITS FILENAME. `_slug`
    folds `/` to `-`, so `a/b-c` and `a-b/c` BOTH resolve to `rail-a-b-c-9.md`:
    trusting the path served `a-b/c#9` the merged record of `a/b-c#9`, and `collect
    --assert-merged` exited 0 for a repo that never merged.

    ONE-SIDED, exactly like `_names_this_repo`: a record whose `REPO=` is absent or
    unreadable has nothing to contradict it — a torn canonical write must still read
    — while a record that names a DIFFERENT repo is not this repo's record.
    """
    try:
        text = art.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    named = _repo_key(read_field(text, "REPO") or "")
    return not named or named == _repo_key(repo or "")


def _record_is_live(art: Path) -> bool:
    """Does this record name a process that is still running?

    A THIN probe on one record — deliberately not `collect`: the resolver is called
    BY `collect`, and a resolver that ran a full two-pass verdict would recurse.
    `EXIT=` is honoured: a record whose writer has exited is finished, whatever its
    (possibly recycled) pid answers, so this never resurrects a completed attempt.
    """
    try:
        text = art.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if _attempt_exits(text):
        return False
    leader = _int_or_none(read_field(text, "PID")) or 0
    child = _int_or_none(read_field(text, "CHILD_PID")) or 0
    age = _age_seconds(read_field(text, "STARTED_AT"))
    if leader <= 0 and child > 0 and read_field(text, "SPAWNING"):
        # ⛔ THE FORK-WINDOW CARRIER — `SPAWNING=1` + `CHILD_PID` with no `PID`, the
        # documented shape a launcher killed between `Popen` returning and its
        # `PID=` append leaves behind (`launch` writes those in two steps). It is a
        # LIVE rail for its whole (queue-length) life and `_collect_read` protects
        # it through this same test; without it here the resolver's live pass skips
        # the record, the canonical fallback reports `lost`, and `launch` starts a
        # SECOND rail — measured. Same one-sided stranger test as the verdict path:
        # it can only say "not ours", never "ours".
        if not _pid_alive(child, ""):
            return False
        return not _pid_is_a_stranger(child, read_field(text, "CHILD_STARTED"),
                                      read_field(text, "CHILD_START_TICKS"))
    if leader <= 0 and child <= 0:
        # A bare `SPAWNING=1` sentinel: the launch is mid-fork, so it IS live until
        # its own TTL expires — the same bound `_collect_read` applies, and for the
        # same reason (an unexpired sentinel would refuse every later `start`
        # forever). A record with neither a pid nor the sentinel names nothing.
        return bool(read_field(text, "SPAWNING")) and age is not None \
            and age <= SPAWNING_TTL_S
    return _rail_alive(leader, child, str(art), age=age)


def _collect_read(state_dir: Path, repo: str, pr: int,
                  artifact: Path | None = None) -> RailState | None:
    """The read itself (see `collect` for the contract, the two passes and the
    attempt-scoping rationale)."""
    art = artifact if artifact is not None else _artifact_for_any_spelling(
        state_dir, repo, pr)
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
            return _exit_verdict(st, snap, repo)

        # No EXIT line yet. Alive ⇒ waiting; gone ⇒ lost (NEVER merged). ⛔ ALIVE
        # IS "THE WRAPPER **OR** THE RAIL": a killed wrapper leaves the rail
        # running, and reading that as `lost` was the P1 (module docstring).
        if probes > 0:
            probed = _identity(snap)
            if _rail_alive(snap.pid, snap.child, str(art),
                           age=_age_seconds(st.since)):
                st.state = WAITING
                # ⛔ A `waiting` VERDICT MUST SAY WHY IT HAS NO VERDICT YET. This
                # arm is the one every live poll takes, and it used to return an
                # EMPTY `reason` — so the most common state the tool reports was
                # the only one that named no mechanism. Measured cost (PR #11128,
                # shard `test (h)`, run 42185502189): the vetting assertion printed
                # `assert 'waiting' == 'merged'` and nothing else, so the lane had
                # no pid, no carrier and no log path to read, and had to re-derive
                # all three. Naming them costs one f-string on a path that already
                # ran two process probes.
                st.reason = (
                    "the rail's carrier is still running, so there is no verdict "
                    "yet (a merge is announced only after the API confirms "
                    f"state=MERGED) — wrapper pid {snap.pid}, rail "
                    f"pid {snap.child}; the rail's own output is {snap.logp}")
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
        merged, sha = _merge_evidence(log, pr, st.repo or repo)
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
    merged, sha = _merge_evidence(log, pr, st.repo or repo)
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


class _GhRun:
    """The fields the `gh` call sites read, from one ALWAYS-RETURNING read.

    ⛔ A MISSING `gh` BINARY IS AN UNREADABLE ANSWER, NOT A CRASH, and it was
    measured on TWO pool boxes before anyone believed it. `subprocess.run(["gh",
    …])` raises `FileNotFoundError` when the executable is not on `PATH`, and
    neither call site caught it — so the launcher died instead of stamping its
    artifact, and every arm of `tests/test_rail_detach.py` read as a failure on
    a runner that has no `gh`. Both measurements are kept here because they are
    the provenance of the same root and neither alone was believed:

    * `test (d)` on the #9018 fallback pool (`pi-runner-vm-2`, 2026-11-03):
      `FileNotFoundError: 'gh'` on a PR whose diff touches neither this tool nor
      its test — a bare `ubuntu-latest` runner with a minimal non-login PATH has
      no `gh`. ⚠️ The two sides recorded this one differently (this branch:
      shard `test (d)`, 21 arms; main/#10622: shard `test (a)`, 20 arms) and
      neither job log is retrievable here, so the ROOT is what is asserted — a
      bare runner with no `gh` — and not the integer.
    * `test (h)` on `pi-runner-vm-6`/Linux-ARM64, run `41732583553` (kept from
      the other side of the #10622 merge): all 20 cases in
      `tests/test_rail_detach.py` killed at this call.

    The intent was already written into `_head_sha` (`else "unreadable"`) and
    into `_resolve_repo`'s `Refused`; only the spawn itself was unguarded. The
    `-> _GhRun` annotation is a CONTRACT, not a hint: this function NEVER
    returns `None` (every spawn failure fabricates a run with a non-zero
    `returncode`), so neither caller may carry an `out is not None` guard. A
    guard that cannot fire is not defence in depth, it is a second, wrong
    description of what this function returns.
    """

    def __init__(self, returncode: int, stdout: str = "", cause: str = "",
                 stderr: str = "", remedy: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.cause = cause
        #: What the OPERATOR should do about it. Carried on the run rather than
        #: hard-coded at the call site, because the remedy differs per cause: a
        #: missing binary is "install it", a missing CWD is "run from a live
        #: checkout", and a 126 for a broken shebang is "repair the install".
        #: One message for three causes tells two of them to fix the wrong thing.
        self.remedy = remedy


def _oserror_remedy(exc: OSError) -> str:
    """What to do about a spawn failure that is NOT a missing binary.

    ⛔ ONE REMEDY FOR EVERY CAUSE TELLS MOST CAUSES TO FIX THE WRONG THING (review,
    `#10777`). The cause is already printed; the remedy has to match it, or the
    operator is walked to the wrong repair — `chmod` for a worktree that no longer
    exists, "reinstall `gh`" for a box that is simply out of process slots.

    ⛔ AND THE ERRNO ALONE IS NOT THE CAUSE EITHER (review, `#10777`): `EACCES`
    says *something* was not permitted, and the object is only PARTLY recoverable —
    `filename` is the CWD for a `chdir` failure and `argv[0]` (the bare name) for
    an exec failure, so the CWD can be blamed when `filename` is not the binary AND
    the errno is one a `chdir` can raise. On the exec side the object is NOT
    recoverable at all (an untraversable `PATH` directory and a non-executable
    file both give `PermissionError(errno=13, filename='gh')`), so that remedy has
    to name both possibilities. Each half is pinned, because each one alone was
    wrong in a different direction: errno-only told an unreadable WORKING DIRECTORY
    to `chmod +x` a `gh` that was fine, and blaming on `filename` alone blamed the
    CWD for a `PATH` entry that is a regular file (`ENOTDIR`, `filename='gh'`).

    ⛔ `ENOENT` IS DELIBERATELY ABSENT FROM THE CWD-SIDE ERRNOS (review, `#10777`).
    Python maps `OSError(ENOENT)` onto `FileNotFoundError`, which `_gh` catches
    BEFORE `except OSError` — so a `chdir` into a directory that is GONE never
    reaches this helper, and an `errno.ENOENT` arm here could not fire from any
    production path (measured: deleting it left the suite green, which is how it
    was found). Its remedy lives on that earlier arm instead. The errnos this
    helper answers for are the ones a `chdir` really delivers, and NONE of them is
    a `FileNotFoundError`: `EACCES`/`EPERM` arrive as `PermissionError`, and
    `ENOTDIR` (its own arm, above) as `NotADirectoryError`.
    """
    target = getattr(exc, "filename", None)
    cwd_side = target not in (None, "", "gh")
    if cwd_side and exc.errno == errno.ENOTDIR:
        return ("the working directory is not a directory — run from a live "
                "checkout, or pass --repo")
    if cwd_side and exc.errno in (errno.EACCES, errno.EPERM):
        return ("the working directory could not be used — run from a live "
                "checkout, or pass --repo")
    if exc.errno in (errno.EACCES, errno.EPERM):
        # ⚠️ `filename` is `argv[0]` here, so it does NOT say which object was
        # denied: an untraversable `PATH` directory and a non-executable file give
        # the same `PermissionError(errno=13, filename='gh')` (measured 2026-11-04).
        # Both remedies are therefore named — `chmod +x` alone is wrong advice for
        # an untraversable directory, and the tool cannot tell the two apart.
        return ("make `gh` executable, and each `PATH` directory it sits in "
                "traversable (`chmod +x`), or pass --repo")
    if exc.errno in (errno.EAGAIN, errno.ENOMEM):
        return ("the box could not spawn a process — retry when it is not loaded, "
                "or pass --repo")
    return "check the working directory and the `gh` install, or pass --repo"


def _gh(argv: list[str], cwd: Path,
        timeout: float | None = None) -> _GhRun:
    """Run `gh` and NEVER raise for a missing binary or an unspawnable argv.

    ⛔ THE BOUND IS PART OF THIS CALL, NOT A CALLER'S ADD-ON (`#10691`'s
    `GH_PROBE_TIMEOUT_S`). Both call sites run on `start`'s path while the
    duplicate-rail flock is HELD, so a `gh` stalled on a saturating cell would
    otherwise delay every later `start` for that PR — turning the #3061 guard into
    the thing that makes the PR unrailable. The bound therefore reaches THIS
    function and answers as a fabricated run (`rc=124`, the shell's own timeout
    code) instead of raising: a caller that needs the bound must not fall back to
    a raw `subprocess.run` and lose the `127`.

    ⛔ TWO SPAWN FAILURES, TWO ANSWERS — never one catch-all (review, `#10777`).
    The caller's remedy for a missing binary is "install the GitHub CLI", which is
    wrong advice — and sends the operator looking in the wrong place — when `gh`
    is installed but not executable (`PermissionError`), when the spawn failed for
    want of resources (an `EAGAIN`/`ENOMEM` from `fork` under cell load), or when
    the CWD is gone. Those return `126` (the shell's "found but could not execute")
    with the real cause, and `127` is reserved for the command genuinely not being
    found.

    ⚠️ `filename == "gh"` earns NONE of that on its own: it separates *exec
    failed* from *`chdir` failed*, and no more. CPython passes the literal argv[0]
    for any exec failure of a bare name, so "is on `PATH` with a dead shebang"
    arrives looking exactly like "is not on `PATH`" — which is why the second
    question is asked separately, of `shutil.which`, before any message says
    "not on PATH" (measured 2026-11-04, sole candidate).

    A failed `chdir` also raises `FileNotFoundError`, but its `filename` is the
    CWD, not `"gh"` — so it lands on the 126 arm and reports the cwd.

    `returncode=127` is the shell's own "command not found", so every existing
    `returncode != 0` branch keeps its meaning and the missing binary cannot be
    mistaken for a successful read — the same shape `tools/who_is_on.py`
    already uses for `git`
    (`except FileNotFoundError: return 127, "", "<bin>: command not found"`).
    """
    try:
        out = subprocess.run(
            ["gh", *argv], cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        # rc=124 is the shell's own `timeout` code, so it can never be confused
        # with the 126/127 spawn answers below — and it carries a `cause` like
        # they do, which is what tells `_resolve_repo` this is a BOUND that fired
        # rather than a real process that exited 124.
        return _GhRun(
            124, cause=f"`gh` did not answer within {timeout}s: {exc}",
            remedy="the call is BOUNDED so a stalled `gh` cannot hold the rail's "
                   "lock — retry when the box is not loaded, or name the target "
                   "yourself with --repo",
        )
    except FileNotFoundError as exc:
        if getattr(exc, "filename", "gh") != "gh":
            # A failed `chdir` raises the SAME exception type, but its `filename`
            # is the CWD. The remedy is a live checkout — never "install the CLI".
            return _GhRun(
                126, cause=f"could not run `gh` in {cwd}: {exc}",
                remedy="the working directory is gone — run from a live checkout, "
                       "or pass --repo",
            )
        # ⛔ `filename == "gh"` CANNOT DISTINGUISH "absent" FROM "not executable":
        # CPython passes the literal argv[0] for ANY exec failure of a bare name, so
        # a `gh` that IS on PATH but whose shebang interpreter is gone arrives here
        # identically (measured 2026-11-04, sole candidate: `shutil.which("gh")`
        # resolves it, `subprocess` does not run it — and CPython's own `PATH` walk
        # only hides the case when a WORKING `gh` sits later on the list, which is
        # `executable_list` built from `os.get_exec_path` at `subprocess.py:1880-1882`).
        # Ask the question
        # separately, so "install the GitHub CLI" is never printed at a binary the
        # operator already has.
        found = shutil.which("gh")
        if found:
            return _GhRun(
                126, cause=f"`gh` is on PATH ({found}) but could not be executed",
                remedy="repair the `gh` install, or pass --repo",
            )
        return _GhRun(127, cause="`gh` is not on PATH",
                      stderr="gh: command not found",
                      remedy="install the GitHub CLI, or pass --repo")
    except OSError as exc:
        return _GhRun(126, cause=f"could not run `gh`: {exc}",
                      remedy=_oserror_remedy(exc))
    return _GhRun(out.returncode, out.stdout or "", stderr=out.stderr or "")


def _resolve_repo(cwd: Path) -> str:
    env_repo = os.environ.get("GH_REPO", "")
    # ⛔ NO `try/except` HERE ANY MORE, AND THAT IS NOT A GATE BEING RELAXED.
    # `_gh`'s `-> _GhRun` contract is that it ALWAYS returns — a missing binary,
    # an unspawnable argv AND a bound that fired each come back as a fabricated
    # run carrying a `cause`. So the two clauses main carried here (`OSError` →
    # "could not run `gh`", `TimeoutExpired` → "did not answer within") are
    # unreachable now, and a guard that cannot fire is a second, wrong
    # description of the function (the same reasoning that removed the
    # `out is not None` guards in the #10622 merge). Their INTENT is preserved
    # one arm down: rc=124 answers "did not answer within", and rc=126/127 with
    # a `cause` answer "could not run `gh`", both naming what to fix.
    out = _gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
              Path(cwd), timeout=GH_PROBE_TIMEOUT_S)
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
    if out.returncode == 124 and out.cause:
        # ⛔ THE BOUND FIRED (main's `#10691` `GH_PROBE_TIMEOUT_S`, kept here).
        # An unnamed target is exactly what the GH_REPO check exists to prevent,
        # so a stalled `gh` must REFUSE rather than detach a rail at a target
        # this launch could not name. Gated on `cause` like every fabricated
        # arm: a REAL `gh` that exited 124 is not a bound, it is the process's
        # own answer, and it falls through to the stderr arm below.
        raise Refused(
            f"`gh repo view` did not answer within {GH_PROBE_TIMEOUT_S}s — refusing to "
            "detach a rail at a target this launch could not name"
        )
    # ⛔ THE FOURTH SIBLING (review cycle 5, finding 2). `cause` is empty for a REAL process,
    # so a real `gh` exiting 124 skipped the arm above and fell through to the generic
    # "is `gh` installed and authenticated? … or install the `gh` CLI" — telling an operator
    # whose binary demonstrably RAN to install it. Exactly the misdiagnosis the 126/127 real
    # arms below exist to prevent, one code over.
    if out.returncode == 124:
        detail = (out.stderr or "").strip().splitlines()
        raise Refused(
            "could not resolve the repo from this checkout (`gh repo view` exited 124"
            + (f": {detail[-1]}" if detail else "")
            + ") — the `gh` on PATH ran and hit its own timeout; pass --repo, or retry "
            "when the API is answering"
        )
    if out.returncode == 127 and out.cause:
        # cause is set ONLY by the arm that FABRICATED this run, so the binary
        # really is not on `PATH`. Names the failing COMMAND, the CAUSE and the
        # REMEDY: a lane reading this must not have to guess which is wrong.
        # ⛔ `installed` is main's word and `not on PATH` is this branch's — the
        # union is asserted by BOTH sides' arms, so dropping either reds a test.
        raise Refused(
            "could not resolve the repo from this checkout (`gh repo view` failed)"
            " — `gh` is not on PATH "
            f"({(out.stderr or 'gh: command not found').strip()}): is the GitHub CLI "
            "installed? pass --repo, or install it"
        )
    if out.returncode == 127:
        # ⛔ A REAL `gh` THAT RAN AND EXITED 127 IS NOT A MISSING BINARY (review,
        # `#10777`). A fabricated run always carries a `cause`; a real one never
        # does — so this arm is the only place a process's own 127 can land. The
        # fleet's own `gh` shim exits 127 with a printed repair (`gh-shim: REFUSING
        # to exec … — unset AGENT_GH_REAL`), and telling that operator "not on PATH —
        # install the GitHub CLI" is a false diagnosis of a binary the same message
        # proves was found and run.
        detail = (out.stderr or "").strip().splitlines()
        raise Refused(
            "could not resolve the repo from this checkout (`gh repo view` exited 127"
            + (f": {detail[-1]}" if detail else "")
            + ") — the `gh` on PATH ran and refused; pass --repo, or repair that `gh`"
        )
    if out.returncode == 126 and out.cause:
        # Found but not runnable — a DIFFERENT remedy from "install the CLI", and
        # the remedy travels on the run because a missing CWD is a third one.
        # ⛔ GATED ON `cause`, for the same reason as the 127 pair above: only a
        # FABRICATED run carries one, so a REAL process that exited 126 must not
        # be told it "could not be executed" — it demonstrably ran (review, #10777).
        raise Refused(
            "could not resolve the repo from this checkout — "
            f"{out.cause} "
            f"({out.remedy or 'pass --repo, or fix the `gh` install'})"
        )
    if out.returncode == 126:
        # ⛔ A REAL `gh` THAT RAN AND EXITED 126 IS NOT "UNEXECUTABLE" (review,
        # #10777). Same shape as the real-127 arm above: a fabricated run always
        # carries a `cause`, a real one never does. The binary ran — that is what
        # proves it is executable — so the only useful thing to print is what IT
        # said, which is the same stderr the generic arm below would surface.
        detail = (out.stderr or "").strip().splitlines()
        raise Refused(
            "could not resolve the repo from this checkout (`gh repo view` exited 126"
            + (f": {detail[-1]}" if detail else "")
            + ") — the `gh` on PATH ran and failed; pass --repo, or repair that `gh`"
        )
    if out.returncode == 0:
        # ⛔ rc=0 IS NOT A SUCCESS HERE (review, `#10777`). The success guard above
        # ALSO requires a non-empty repo, so a `gh` that exited 0 and printed
        # nothing lands here — and it RAN, which is what proves it is installed.
        # The generic arm below asks "is `gh` installed and authenticated?" and
        # offers "install the `gh` CLI", the same message-vs-exit-code dishonesty
        # this PR removes one branch above.
        raise Refused(
            "could not resolve the repo from this checkout (`gh repo view` exited 0 "
            "but printed no repository) — the `gh` on PATH ran, so this is not an "
            "install problem; pass `--repo owner/name` to name the artifact yourself"
        )
    # Every other NON-ZERO exit keeps main's stderr detail: the last line is the
    # only thing that says WHY `gh repo view` failed (auth, network, no remote).
    # (rc=0 is handled above, so "non-zero" is a precondition this arm now
    # actually has, not just a comment.)
    detail = (out.stderr or "").strip().splitlines()
    # ⛔ BOTH SIDES' WORDING (#10622 merge): the branch side asserts the message
    # says WHAT to fix ("installed"), main's carries the stderr detail plus the
    # two remedies. Taking either side alone drops the operator's remedy.
    raise Refused(
        "could not resolve the repo from this checkout (`gh repo view` failed"
        + (f": {detail[-1]}" if detail else "")
        + ") — is `gh` installed and authenticated? pass `--repo owner/name` to name "
        "the artifact yourself, or install the `gh` CLI"
    )


def _head_sha(cwd: Path, repo: str, pr: int) -> str:
    """The PR's head, or the literal `unreadable` — NEVER an empty string.

    INFORMATIONAL ONLY: `HEAD` gates nothing — and BOUNDED, because this call is
    made while the duplicate-rail flock is held (see `GH_PROBE_TIMEOUT_S`). A
    `gh` stalled on a saturating cell would otherwise delay every later `start`
    for that PR, turning the #3061 guard into the thing that makes the PR
    unrailable.

    ⛔ THE EMPTY-STRING HOLE IS THE SIBLING OF `_resolve_repo`'S (review,
    `#10777`). A `gh` that exits 0 and prints nothing passes a bare
    `returncode == 0` guard, so this function answered `""` — which is not a
    named degraded state, it is silence: `launch()` then stamps `HEAD=` (blank)
    into the artifact and `collect` reports nothing for it, so a reader cannot
    tell "the head could not be read" from "the field was never written". The
    sibling call site refuses by name for the same condition, so the two halves
    of one seam disagreed about what the same silence means. Measured with a
    stub `gh` that exits 0 silently: this returned `""` while `_resolve_repo`
    raised `Refused` ("exited 0 but printed no repository").

    ⛔ NO `try/except` HERE EITHER, for the same contract reason as
    `_resolve_repo`: `_gh` ALWAYS returns, so a spawn failure, an unspawnable
    argv and a fired bound are already non-zero runs — each of which lands on
    `unreadable` below. The escaped-`PermissionError` and `TimeoutExpired`
    clauses main carried are unreachable once the whole spawn is answered, and
    their INTENT (never crash `launch()` over an informational read) is what the
    `else "unreadable"` arm already delivers.
    """
    out = _gh(["pr", "view", str(pr), "--repo", repo, "--json", "headRefOid",
               "-q", ".headRefOid"], Path(cwd), timeout=GH_PROBE_TIMEOUT_S)
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() \
        else "unreadable"


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
        # ⛔ REFUSE A SHARED PATH OCCUPIED BY ANOTHER REPO'S RECORD, BEFORE ANY WRITE.
        # `_slug` folds `/` to `-`, so `a/b-c#9` and `a-b/c#9` are ONE artifact, ONE
        # log and ONE `.lock`, and the write below is unconditional (`write_text` of
        # the artifact, TRUNCATION of the log). Once `collect` correctly declined to
        # serve `a-b/c` the record naming `a/b-c`, `prior` was None — so the guard
        # passed and the launch REWROTE that live record's `REPO=`, truncated its log
        # (destroying a real `✅ merged …` line for good) and ran a second rail whose
        # appends interleave in the same two files. Silent destruction of a live
        # record AND two rails on one PR: measured, and both are the classes this
        # module exists to refuse. `origin/main` refused this launch only because it
        # read the shared record as this repo's own — the refusal was the right
        # answer for the wrong reason, and it must not be re-opened by making the
        # identity check correct.
        # ⛔ WHAT THE ARTIFACT **ATTESTS**, resolved once, because "the artifact is
        # present" does NOT mean "it attests THIS repo". `_record_names_repo` is
        # one-sided by design — a header write cut before its `REPO=` line must still
        # read — so a TORN record is admitted, and gating the log check on
        # `not art.exists()` let exactly that shape skip BOTH guards and truncate the
        # colliding repo's log. `art_key` is therefore the positive attestation: ""
        # means the path names nothing (absent, torn, or unreadable), which is not a
        # claim this repo may act on.
        art_key = ""
        if art.exists():
            try:
                art_key = _repo_key(read_field(
                    art.read_text(encoding="utf-8", errors="replace"), "REPO") or "")
            except OSError:
                art_key = ""
            if art_key and art_key != repo:
                raise Refused(
                    f"the artifact path {art} is occupied by ANOTHER repo's record — the "
                    "path, the log and the lock are shared, so launching here would "
                    "overwrite that rail's record and truncate its log; two repos whose "
                    "`owner/name` slug identically cannot be railed separately in one "
                    "--state-dir. Use a separate --state-dir for this repo."
                )
        # ⛔ AND THE LOG IS THE OTHER HALF OF THAT SHARED PATH — checked whenever the
        # artifact does not POSITIVELY attest this repo: absent (the state this tool's
        # OWN remedy creates — `rm <artifact>` clears a stale claim and stops nothing)
        # or torn (no `REPO=` to attest with). The log is not removed with either, and
        # the write below TRUNCATES it (`logp.open("w")`), so another repo's
        # `✅ merged …` line would go silently. One-sided, through the rail's own
        # evidence line: a log with no attributable `repo <x>` line contradicts
        # nothing (the same-repo `rm` residual stays exactly what it documents), while
        # one naming a DIFFERENT repo is provably not ours to truncate.
        if art_key != repo and logp.exists():
            try:
                said = logp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                said = ""
            if said and not _names_this_repo(said, repo):
                raise Refused(
                    f"the log {logp.name} belongs to ANOTHER repo's rail (its own "
                    "`admin-merge: repo <x>` line names a different target) — the "
                    f"artifact {art.name} does not attest this repo, and launching "
                    "here would truncate that rail's output, including any merge "
                    "evidence it holds. Use a separate --state-dir for this repo."
                )
        # ⛔ AND THE GUARD ASKS A LIVENESS QUESTION, NOT A VERDICT ONE. `collect`
        # answers with the MERGED record when a merged one coexists with a live one
        # (correct: a merge is monotone), and reading that as "not WAITING ⇒ free"
        # let `launch` truncate the LIVE record and start a second rail — the #3061
        # collision this guard exists to refuse, measured. So the guard looks for ANY
        # record of this PR that is waiting, and refuses on that.
        prior = _waiting_record(state_dir, repo, pr)
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
        #
        # ⛔ AND THE COUNT IS OF REPOS, NOT OF FILES. Two records for one PR and
        # one repo is now the NORMAL shape while the fleet is part way through the
        # spelling change: an old copy of this tool wrote `rail-github.com-o-r-7.md`
        # beside the canonical `rail-o-r-7.md`. Counting FILES read that
        # compatibility state as ambiguity and fell through to `gh` — so the verb
        # that exists to work without the network exited 2 ("gh cannot resolve this
        # repo") about a record that names its repo on disk. Grouping by
        # `_repo_key` keeps "two REPOS for one PR number" as the only ambiguity, and
        # that is the case the `>1` arm is actually for.
        cands = sorted(Path(sd).glob(f"rail-*-{a.pr}.md")) if Path(sd).exists() else []
        by_repo: dict[str, str] = {}
        for cand in cands:
            try:
                text = cand.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            raw = read_field(text, "REPO") or ""
            key = _repo_key(raw)
            # The RAW value is kept as the repo to resolve, so the `REPO=` line the
            # record already carries is what `collect` is asked about — `_repo_key`
            # decides identity here, it does not rewrite the target.
            if key and key not in by_repo:
                by_repo[key] = raw
        if len(by_repo) == 1:
            repo = next(iter(by_repo.values()))
        elif not cands:
            # ⛔ ZERO CANDIDATES IS AN ANSWER, NOT A REASON TO ASK GITHUB. The
            # artifact filename is `<repo slug>-<pr>`, so an artifact this glob does
            # not match cannot exist for ANY repo — the PR has never been railed
            # here, and that is `absent` (3) whatever `gh` would have said. Asking
            # `gh` first made the answer depend on the network: with `gh` failing,
            # an empty state dir exited 2 (environment error) instead of 3. The
            # `>1 REPO` case still resolves, because two repos' artifacts for one PR
            # number genuinely does not name a target.
            print(_absent_document(a.pr, a.json))
            return 3
    if not repo:
        try:
            repo = _resolve_repo(Path(os.getcwd()))
        except Refused as e:
            print(f"rail_detach: ✗ {e}", file=sys.stderr)
            return 2
    st = collect(sd, repo, a.pr)
    if st is None:
        print(_absent_document(a.pr, a.json))
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
