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
then `EXIT=`/`FINISHED_AT=` when the rail exits. `VERDICT` and `MERGE_SHA` are
DERIVED at collect time from the rail's own log lines (`✅ merged PR #N at <sha>`,
`ALREADY MERGED`) rather than re-asking the API — the artifact must be readable
when the network is not, and the rail's own words are the primary record.

STATES
------
    waiting   the rail process is alive (own session) and has no EXIT yet
    merged    EXIT=0
    refused   EXIT != 0 (the rail's own fail-closed verdict; reason carried)
    lost      the pid is gone with NO EXIT line — killed outright. NEVER reported
              as merged: a rail that vanished has no verdict, and inventing one is
              the #2235 fail-open shape.

WHY NOT `nohup`/`&`
-------------------
`&` inherits the lane's process GROUP, so the harness's group kill takes the rail
with it; `nohup` only ignores SIGHUP, it does not leave the group. `setsid`
(here via `start_new_session=True`) puts the rail in its own session, which is
what survives both the SIGKILL to the lane's group and the lane process exiting.
`tests/test_rail_detach.py` proves the difference by re-running the SAME launch
with detachment off and killing the launcher's group.

Stdlib only, and Python >= 3.12 (the repo contract — the module-level
`from datetime import UTC` is 3.11+, so the #5128 guard below refuses an older
interpreter by NAME instead of dying on that import).
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/rail_detach.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/rail_detach.py`"
    )

import argparse
import errno
import fcntl
import json
import os
import re
import shutil
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


def _pid_alive(pid: int, marker: str) -> bool:
    """Alive AND still ours — `os.kill(pid, 0)` alone reports a RECYCLED pid as
    a live rail, which would report `waiting` forever on a dead rail."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A ZOMBIE still answers kill(0) and its cmdline is EMPTY — reporting it alive
    # would leave a killed rail reading `waiting` forever. /proc/<pid>/stat field 3
    # is the state letter; `Z` is gone-as-far-as-a-verdict-is-concerned.
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        if stat.rsplit(") ", 1)[1].split()[0] == "Z":
            return False
    except (OSError, IndexError):
        pass
    cmdline = Path(f"/proc/{pid}/cmdline")
    if cmdline.exists():
        try:
            raw = cmdline.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            return False
        # An EMPTY cmdline is a process that exists but has not exec'd yet (the
        # fork/exec window) — alive, not dead. Reporting `lost` there is how a
        # healthy rail reads as killed (#observed live 2026-11-03).
        if not raw.strip():
            return True
        # The child's argv carries the rail path; a recycled pid will not.
        return Path(marker).name in raw or marker in raw
    return True  # no /proc (non-Linux): the kill probe is all we have


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
    network, no process signals. Returns None when no artifact exists."""
    art = artifact_path(state_dir, repo, pr)
    if not art.exists():
        return None
    text = art.read_text(encoding="utf-8", errors="replace")
    logp = Path(read_field(text, "LOG", str(log_path(state_dir, repo, pr))))
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

    # No EXIT line yet. Alive ⇒ waiting; gone ⇒ lost (NEVER merged).
    if _pid_alive(pid, str(art)):
        st.state = WAITING
        return st
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
    """
    target = getattr(exc, "filename", None)
    cwd_side = target not in (None, "", "gh")
    if cwd_side and exc.errno == errno.ENOTDIR:
        return ("the working directory is not a directory — run from a live "
                "checkout, or pass --repo")
    if cwd_side and exc.errno in (errno.ENOENT, errno.EACCES, errno.EPERM):
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


def _gh(argv: list[str], cwd: Path) -> _GhRun:
    """Run `gh` and NEVER raise for a missing binary or an unspawnable argv.

    ⛔ TWO SPAWN FAILURES, TWO ANSWERS — never one catch-all (review, `#10777`).
    The caller's remedy for a missing binary is "install the GitHub CLI", which is
    wrong advice — and sends the operator looking in the wrong place — when `gh`
    is installed but not executable (`PermissionError`), when the spawn failed for
    want of resources (an `EAGAIN`/`ENOMEM` from `fork` under cell load, the same
    condition this file already documents at the `SPAWNING` sentinel above), or
    when the CWD is gone. Those return `126` (the shell's "found but could not
    execute") with the real cause, and `127` is reserved for the command genuinely
    not being found.

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
    out = _gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd)
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
    if out.returncode == 127 and out.cause:
        # cause is set ONLY by the arm that FABRICATED this run, so the binary
        # really is not on `PATH`. Names the failing COMMAND, the CAUSE and the
        # REMEDY: a lane reading this must not have to guess which is wrong.
        raise Refused(
            "could not resolve the repo from this checkout (`gh repo view` failed)"
            " — `gh` is not on PATH "
            f"({(out.stderr or 'gh: command not found').strip()}): pass --repo, or "
            "install the GitHub CLI"
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
    if out.returncode == 126:
        # Found but not runnable — a DIFFERENT remedy from "install the CLI", and
        # the remedy travels on the run because a missing CWD is a third one.
        raise Refused(
            "could not resolve the repo from this checkout — "
            f"{out.cause or '`gh` could not be executed'} "
            f"({out.remedy or 'pass --repo, or fix the `gh` install'})"
        )
    # Every other non-zero exit keeps main's stderr detail: the last line is the
    # only thing that says WHY `gh repo view` failed (auth, network, no remote).
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
    out = _gh(["pr", "view", str(pr), "--repo", repo, "--json", "headRefOid",
               "-q", ".headRefOid"], cwd)
    return out.stdout.strip() if out.returncode == 0 else "unreadable"


def _shquote(value: object) -> str:
    """POSIX single-quote `value` for the child's `bash -c` body."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def child_script(rail: Path, repo: str, pr: int, logp: Path, art: Path,
                 extra: list[str]) -> list[str]:
    """The detached rail's argv. NOTHING but the one lander is invoked: no gh
    merge, no flags added, no gate relaxed. `extra` is passed through verbatim
    because it is the operator's own rail invocation (e.g. `--dry-run`)."""
    rail_args = " ".join(
        _shquote(a) for a in [str(rail), str(pr), "--repo", repo, *extra])
    body = (
        f"printf 'CHILD_STARTED=%s\\n' \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" >> {_shquote(art)}\n"
        f"bash {rail_args} >> {_shquote(logp)} 2>&1\n"
        f"rc=$?\n"
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
