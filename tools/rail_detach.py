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
then `CHILD_PID=` (written by the RAIL itself) and `CHILD_STARTED=`,
then `EXIT=`/`FINISHED_AT=` when the rail exits. `VERDICT` and `MERGE_SHA` are
DERIVED at collect time from the rail's own log lines (`✅ merged PR #N at <sha>`,
`ALREADY MERGED`) rather than re-asking the API — the artifact must be readable
when the network is not, and the rail's own words are the primary record.

STATES
------
    waiting   the rail is still RUNNING (own session) and has no EXIT yet
    merged    EXIT=0
    refused   EXIT != 0 (the rail's own fail-closed verdict; reason carried)
    lost      nothing that carries the verdict is left, and there is NO EXIT line
              — killed outright. NEVER reported as merged: a rail that vanished
              has no verdict, and inventing one is the #2235 fail-open shape.

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
    2  environment error: the repo could not be named, or the interpreter is < 3.12
    3  `collect` found no artifact for that PR
    4  `start` refused (a rail is already live, or a forbidden bypass is set)
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

# Every `gh` call here is INFORMATIONAL and every one of them is bounded. An
# unbounded one is not a slow read, it is a WEDGE: `_head_sha` runs while this
# process holds the duplicate-rail flock, so a `gh` stalled on a saturating cell
# blocks that lock and every later `start` for the PR queues behind it — the
# guard becomes the thing that makes the PR unrailable. `collect` deliberately
# never calls `gh` (it answers from the artifact), so a status read is unaffected.
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
# admin-merge.sh:9033 — the SAME success with the merge commit deliberately not
# printed (it was unreadable). A merge with no sha is still a merge; the sha is
# simply absent, and the artifact says so rather than substituting the head.
MERGED_NO_SHA_RE = re.compile(r"✅ merged PR #(\d+) \(state=")
ATTEMPT_MARK = "===== rail attempt started"
ALREADY_MERGED_RE = re.compile(r"PR #(\d+) is ALREADY MERGED")
REFUSAL_RE = re.compile(r"(?:✗|BLOCK|REFUSING|REFUSED)[^\n]*")

WAITING, MERGED, REFUSED, LOST = "waiting", "merged", "refused", "lost"

# Memoised answer to "can this box's `ps` report a pid's state letter?".
_PS_USABLE: bool | None = None


# ── paths ────────────────────────────────────────────────────────────────────
def _slug(repo: str) -> str:
    return repo.replace("/", "-")


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
    """
    if pid <= 0 or pgid <= 0:
        return False
    try:
        return os.getpgid(pid) == pgid
    except OSError:
        return False


def _pid_alive(pid: int, marker: str = "", *, pgid: int | None = None) -> bool:
    """Alive AND still ours — `os.kill(pid, 0)` alone reports a RECYCLED pid as
    a live rail, which would report `waiting` forever on a dead rail.

    `pgid` swaps the "is it ours" test from an argv marker to GROUP MEMBERSHIP —
    required for the rail itself (see the module docstring): its argv is whatever
    `admin-merge.sh` last exec'd, while its group is fixed at fork time.
    """
    if pid <= 0:
        return False
    if pgid is None:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
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
        # The child's argv carries the rail path; a recycled pid will not.
        return Path(marker).name in raw or marker in raw
    if proc_letter is not None:
        # procfs answers but the cmdline did not: the process is real and not a
        # zombie, and its argv was unreadable — keep the rail's claim.
        return True
    # ⛔ NO PROCFS: the kill probe above is NOT enough — it answers yes for a
    # zombie, so a killed rail would read `waiting` forever (see `_ps_stat`).
    # Only the STAT letter is consulted here, never the command line: bash
    # may have exec'd the body's last command, so a LIVE rail can legitimately
    # present an argv that does not carry the artifact path.
    stat = _ps_stat(pid)
    if stat is None:
        return True  # unmeasurable ⇒ keep the rail's claim alive (see _ps_stat)
    if not stat:
        return False  # `ps` knows no such pid ⇒ it is gone
    return not stat.startswith("Z")


def _rail_alive(leader: int, child: int, marker: str) -> bool:
    """Is the rail's WORK still running? Two processes carry it, and they do not
    die together: the wrapper (`leader`, which writes `EXIT=`) and the rail
    itself (`child`, which runs `admin-merge.sh`). Probing only the wrapper read
    `lost` for a rail that had merely lost its wrapper and was still merging
    (measured — see the module docstring). Either one alive means `waiting`.

    An artifact from before `CHILD_PID=` has no rail pid; it falls back to the
    wrapper-only probe, which is what it was written under.
    """
    if _pid_alive(leader, marker):
        return True
    return leader > 0 and child > 0 and _pid_alive(child, pgid=leader)


def _int_or_none(s: str) -> int | None:
    """A torn/garbled kv value must not crash the read-only verb. `printf` writes
    `EXIT=%s` and its argument in separate expansions, so a group kill can land
    between them and leave `EXIT=` — the fleet beat then gets a traceback instead
    of a state, which is worse than any verdict."""
    try:
        return int((s or "").strip())
    except (TypeError, ValueError):
        return None


def _merge_evidence(log: str) -> tuple[str, str]:
    """(state, sha) from the rail's OWN words, or ('', '') when it said nothing.
    ⛔ A CODE-0 EXIT IS NOT A MERGE: `admin-merge.sh --dry-run` (and --help,
    --print-bounds) exit 0 having merged nothing, and the launcher deliberately
    passes operator args through. Merged requires the rail to SAY merged."""
    m = MERGED_AT_RE.search(log)
    if m:
        return MERGED, m.group(2)
    if ALREADY_MERGED_RE.search(log):
        # Already merged before this attempt: that merge commit belongs to the
        # earlier merge and is NOT this branch head (under a squash the head is
        # never on main — admin-merge.sh:9033). Absent, never invented.
        return MERGED, ""
    if MERGED_NO_SHA_RE.search(log):
        return MERGED, ""
    return "", ""


# ── collect ──────────────────────────────────────────────────────────────────
@dataclass
class RailState:
    pr: int
    state: str
    run_ids: list[str] = field(default_factory=list)
    since: str = ""
    head: str = ""
    pid: int = 0
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
            "exit_code": self.exit_code,
            "merge_sha": self.merge_sha,
            "reason": self.reason,
            "artifact": self.artifact,
            "log": self.log,
        }


def collect(state_dir: Path, repo: str, pr: int) -> RailState | None:
    """Read the artifact and the rail's log. NO side effects: no writes, no
    network, no process signals. The only external call is a local `ps` state
    read, and only on a box without procfs. Returns None when no artifact
    exists."""
    art = artifact_path(state_dir, repo, pr)
    if not art.exists():
        return None
    logp_default = log_path(state_dir, repo, pr)
    # ⛔ TWO PASSES, AND THE SECOND IS NOT A RETRY OF A FAILURE — it closes a
    # STALE READ. `text` is read BEFORE the liveness probe, so a rail that writes
    # `EXIT=` and exits *while* that probe runs is seen as GONE holding a read
    # that predates its EXIT: `merged` with `exit_code=None` and the false reason
    # "the EXIT line was never written", for a rail that wrote one. The state was
    # still `merged` (the rail's own success line was in the log) so no gate
    # moved, but the exit code and the explanation were both wrong — and it is
    # not theoretical: it made `test_merged_state_names_the_sha_the_rail_printed`
    # flake 2/2 at load 27 on 2026-11-03 while passing in isolation.
    #
    # So the "gone" conclusion is only drawn from an artifact re-read AFTER the
    # probe. One extra read, and only on the path where we are about to declare a
    # rail dead.
    for _ in range(2):
        text = art.read_text(encoding="utf-8", errors="replace")
        logp = Path(read_field(text, "LOG", str(logp_default)))
        log = ""
        if logp.exists():
            full = logp.read_text(encoding="utf-8", errors="replace")
            # ⛔ THE LOG IS APPEND-ONLY ACROSS ATTEMPTS. Parsing the whole file lets a
            # DEAD attempt inherit a PREVIOUS attempt's `✅ merged` line and be
            # reported merged — evidence forged by concatenation. Every verdict read
            # is scoped to the LAST attempt.
            cut = full.rfind(ATTEMPT_MARK)
            # ⛔ NO MARK ⇒ NO SCOPED EVIDENCE. Our launcher always writes the mark
            # before it spawns, so a log without one is not this attempt's: reading it
            # whole would let a dead attempt inherit ANY earlier `✅ merged` line — the
            # forged-evidence shape the scoping exists to prevent. Fail CLOSED (empty
            # evidence ⇒ refused/lost), never "read everything".
            log = full[cut:] if cut >= 0 else ""

        exits = read_all(text, "EXIT")
        pid = _int_or_none(read_field(text, "PID", "0")) or 0
        child = _int_or_none(read_field(text, "CHILD_PID", "0")) or 0
        st = RailState(
            pr=pr,
            state=WAITING,
            run_ids=sorted(set(RUN_ID_RE.findall(log)), key=lambda s: int(s)),
            since=read_field(text, "STARTED_AT"),
            head=read_field(text, "HEAD"),
            pid=pid,
            artifact=str(art),
            log=str(logp),
        )

        if exits:
            code = _int_or_none(exits[-1])
            st.exit_code = code
            merged, sha = _merge_evidence(log)
            if merged == MERGED:
                st.state, st.merge_sha = MERGED, sha
                if not sha:
                    st.reason = ("merged; the rail could not read the merge commit "
                                 "(admin-merge.sh deliberately does not print it)")
            else:
                # Exit 0 with no merge line: the rail DECLINED to merge (a --dry-run,
                # a --print-bounds, a help exit) or refused. Either way there is no
                # merge, and saying `merged` would release the next beat's gate on
                # evidence that does not exist.
                st.state = REFUSED
                hits = REFUSAL_RE.findall(log)
                st.reason = (hits[-1].strip()[:400] if hits else
                             (f"exited {code} without a merge line — the rail did not "
                              "merge (e.g. --dry-run), so this is not a merge"
                              if code == 0 else "no refusal line in the log"))
            return st

        # No EXIT line yet. Alive ⇒ waiting; gone ⇒ lost (NEVER merged). ⛔ ALIVE
        # IS "THE WRAPPER **OR** THE RAIL": a killed wrapper leaves the rail
        # running, and reading that as `lost` was the P1 (module docstring).
        if _rail_alive(pid, child, str(art)):
            st.state = WAITING
            return st

        # Everything below concludes from the ABSENCE of `EXIT=`, so it needs a
        # read taken after the probe that just said the writer is gone. Loop once
        # on the fresh text when the artifact has moved; break when it has not
        # (including `CHILD_STARTED=`, which the child appends and which changes
        # nothing a verdict reads).
        fresh = art.read_text(encoding="utf-8", errors="replace")
        # `CHILD_PID=` is the same race one process over: the wrapper can be
        # killed in the instant between the spawn and the child naming itself, so
        # a first read that missed it is not yet "no rail".
        if (read_all(fresh, "EXIT") != exits
                or (not child and read_field(fresh, "CHILD_PID"))):
            continue
        break
    if pid == 0 and read_field(text, "SPAWNING") and not exits:
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
        age = _age_seconds(st.since)
        if age is not None and age <= SPAWNING_TTL_S:
            st.state = WAITING
            st.reason = "launching (no pid recorded yet)"
            return st
        st.state = LOST
        st.reason = ("the launch died between the artifact write and the spawn — "
                     "no rail ever started (SPAWNING never became a pid)")
        return st
    # A merge the rail ANNOUNCED and then died before recording the exit code is
    # still a merge — the announcement is made only after the API confirmed
    # `state=MERGED`. Reading it is not a gate being relaxed: the rail's gates ran
    # to completion, only the bookkeeping line was lost. Anything short of that
    # announcement stays `lost` (a vanished rail has NO verdict — #2235).
    merged, sha = _merge_evidence(log)
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


def _resolve_repo(cwd: Path) -> str:
    env_repo = os.environ.get("GH_REPO", "")
    try:
        out = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            cwd=str(cwd), capture_output=True, text=True,
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
    raise Refused("could not resolve the repo from this checkout (`gh repo view` failed)")


def _head_sha(cwd: Path, repo: str, pr: int) -> str:
    """The head sha for the artifact, or `unreadable`. INFORMATIONAL ONLY —
    nothing gates on it — and BOUNDED, because this call is made while the
    duplicate-rail flock is held (see GH_PROBE_TIMEOUT_S)."""
    try:
        out = subprocess.run(
            ["gh", "pr", "view", str(pr), "--repo", repo, "--json", "headRefOid",
             "-q", ".headRefOid"],
            cwd=str(cwd), capture_output=True, text=True,
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
                 extra: list[str]) -> list[str]:
    """The detached rail's argv. NOTHING but the one lander is invoked: no gh
    merge, no flags added, no gate relaxed. `extra` is passed through verbatim
    because it is the operator's own rail invocation (e.g. `--dry-run`).

    The rail runs as a BACKGROUND job so this wrapper can `wait` for its exit
    code and record `EXIT=` — and it records its OWN pid (`$$`) before `exec`-ing
    the lander, so a wrapper killed at any point after the fork still leaves a name
    for a rail that is running (`$!` would be written BY the wrapper, i.e. lost in
    exactly the case this exists for). `exec` keeps the pid, so `CHILD_PID=` names
    the real rail."""
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
        f"wait \"$child\"; rc=$?\n"
        f"printf 'EXIT=%s\\nFINISHED_AT=%s\\n' \"$rc\" \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" >> {_shquote(art)}\n"
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
    repo = repo or _resolve_repo(Path(cwd))
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
            raise Refused(
                f"a rail is ALREADY LIVE for PR #{pr} (pid {prior.pid}, since "
                f"{prior.since}) — two rails on one PR is the #3061 collision; "
                f"collect it with: collect {pr}"
            )

        head = _head_sha(Path(cwd), repo, pr)
        # A fresh attempt starts a fresh record; the previous attempt stays in the
        # .log (the child appends), which is what the merged-sha parse reads.
        # `SPAWNING=1` is written BEFORE the spawn, because the window between the
        # two is a rail that exists but has no pid yet — and `collect` must not
        # call that `lost` (nor let a second rail through).
        art.write_text(
            f"# rail artifact — {repo}#{pr}\n"
            f"PR={pr}\nREPO={repo}\nHEAD={head}\nSTARTED_AT={_iso_now()}\n"
            f"CWD={cwd}\nLOG={logp}\nSPAWNING=1\n"
            f"RAIL_ARGS={' '.join([str(pr), '--repo', repo, *extra])}\n",
            encoding="utf-8",
        )
        with logp.open("a", encoding="utf-8") as fh:
            fh.write(f"\n===== rail attempt started {_iso_now()} (detached) =====\n")

        argv = child_script(rail, repo, pr, logp, art, extra)
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
