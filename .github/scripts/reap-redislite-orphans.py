#!/usr/bin/env python3
"""Reap redislite servers that are PROVABLY orphaned — never a live peer's (#3653).

⛔ SCOPE of that promise: THIS tool's two arms. It does not reach the 30-minute
`find "$TMPD" -maxdepth 1 -type d -name "tmp*" -mmin +30 -exec rm -rf` litter
sweep that shares the same step — that arm deletes by mtime with no liveness
check at all and CAN unlink a live peer's socket directory (#3653's remaining
half; the arithmetic is in `DEFAULT_MAX_AGE_MINUTES`). Read the headline as
"never by this tool's own decision", not as "nothing in this step can".

## Why this exists

`python-ci.yml` cleaned stale embedded-redis litter with a bare, host-global:

    pkill -f "redis-server unixsocket"

`pkill -f` matches **every** process on the box whose argv contains that string.
On this repo's CI it therefore matched a *concurrent* leg's live redislite
instance as readily as a leaked one. Killing it is not silent at the client:
redislite unlinks its socket on a clean (SIGTERM) shutdown, so the next connect
gets `Error 2 connecting to <socket>. No such file or directory` — ENOENT, not
ECONNREFUSED — and the victim leg reds with a long tail of "seeded state
vanished" assertions that have nothing to do with the diff under test.

That the legs really do share a host is not an inference: the leg's own orphan
gate printed

    redislite orphan gate: the hygiene end-sweep deferred to last-suite-standing
    (other_suites=10 foreign_pids=10)

i.e. **ten other live pytest suites** were present on the same machine while
that leg ran (`python-ci.yml`, `orphan-bound.sh`). With four jobs per run
(`test`, `test-slow`, `test-concurrency-falkor`, `test-carve-out`) each running
that `pkill` in its setup, any leg that starts later kills the embedded servers
of every leg that started earlier.

## How a server is IDENTIFIED — and why it is not `argv[0]` (#10502)

The identity of a redis server comes from something no wrapper can set: the
kernel's own name for the process (`/proc/<pid>/comm`, and the resolved
executable as the last resort). Both are written by the KERNEL at `exec`, from
the binary's file name — a wrapper that `exec`s the server inherits them, while
its own `argv` is whatever it passed. Identity used to be read out of `argv[0]`
only, and that is how this tool came to print

    applied: 0 candidate(s), 0 reaped        # exit 0, no REAP/KEEP line at all

on a cell carrying **1,555 `redis-server` processes, 1,537 of them orphaned**
(2026-11-21; 589 on the first report, 2026-11-01). `ps -eo comm` saw every one of
them. Zero `REAP`/`KEEP` lines is the tell: a run that *classifies and declines*
prints one line per row, so no lines at all means no rows were ever built — the
filter dropped the population before `classify()` saw it.

`argv[0]` is still read first (it is the cheap, common case), but it is no
longer the authority, and a command line that arrived as ONE argv element
(`argc == 1`, the shape the runner actually produces:
`argv[0] == '<bin>/redis-server unixsocket:<dir>/redis.socket'`) is tokenised
before the socket directory is read out of it.

Every run now prints the pre-filter **census** — every process the kernel names
`redis-server`, read straight out of `comm` BEFORE any candidate filter runs —
so `0 candidate(s)` is never again indistinguishable from "could not see the
host". When a census member cannot be accounted for as a candidate or as a
stated exclusion, the run says so and exits 3.

## What it does instead

It kills a redislite server only when the server is provably nobody's:

1. **The socket directory is gone.** The instance can serve nothing, and a live
   suite's directory is never removed (the session teardown refuses while a
   live server holds the root — `#3752`). This is the actual #1005 litter: the
   tempdirs left by failed runs.
2. **The process is older than every embedded lane's OUTER cancel bound.** A
   redislite server older than the widest `jobs.<job_id>.timeout-minutes` among
   the jobs that can OWN one cannot belong to a running leg, because GitHub
   cancels the job at that bound. "Can own one" is the embedded lanes — the
   jobs that invoke this reaper, plus every job that opts into the URI-less
   embedded run (`TORTOISE_TEST_CARVE_OUT`, how a suite reaches a redislite
   server at all: `tests/_embedded.py::_carve_out_opted_in`) — and NOT merely
   the five that run this step: this tool enumerates the host's process table,
   so it can meet a server belonging to a job that never installs it. It is a
   JOB bound, not a `matrix.watchdog_minutes` bound: the watchdog only stops
   pytest, so it bounds neither the setup/pre-phase around it nor — see the
   derivation at `DEFAULT_MAX_AGE_MINUTES` — the 55 minutes the repo's own
   policy lets that watchdog reach.

Everything else is KEPT, including every unmeasurable case (unreadable
`/proc`, unknown age, unparsable argv): a cleanup step that fails is a leak,
which the orphan gate reports — a cleanup step that fails *open* is a false RED
on somebody else's PR, which is the defect this file removes.

Deliberately stdlib-only and 3.9-clean: it runs in a CI setup step as
`python3 .github/scripts/reap-redislite-orphans.py`, before anything needs the
project's own interpreter.

Usage:
    python3 .github/scripts/reap-redislite-orphans.py                  # dry run
    python3 .github/scripts/reap-redislite-orphans.py --apply          # kill
    python3 .github/scripts/reap-redislite-orphans.py --max-age-minutes 95 --apply

Exit codes: 0 = ran (dry or apply) — INCLUDING when it MEASURED the host and
found no litter; 2 = something this tool was told to read could not be read
(`--proc-json`, or the process inventory itself: neither `/proc` nor `ps`
answered — `ps` missing AND `ps` exiting non-zero are the same fact — so NOTHING
was measured and nothing was reaped); 3 = the census and the inventory disagree:
processes are NAMED `redis-server` by the kernel but were neither enumerated as
candidates nor excluded with a stated reason, so this run did not see the
population it reported on. Exit 3 is the #10502 signal — a parse/filter gap must
never read as a measured zero. Every other unmeasurable case is per-row and
KEEPS its process with a printed reason, which is still exit 0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
from typing import NamedTuple

# The redislite signature: the redis-server binary plus a unix socket in argv.
_IS_REDIS = re.compile(r"(?:^|/)(redis-server)$")
#: The kernel's name for a redis server (`/proc/<pid>/comm`, or the basename of
#: the resolved `/proc/<pid>/exe`). Anchored, and followed by end-of-string,
#: whitespace or a colon: that accepts the name redis gives itself when built
#: with `setproctitle` ("redis-server *:6379", and its 15-byte `comm`
#: truncation "redis-server *:"), while refusing a name that merely starts the
#: same ("redis-serverish", "redis-server-x").
_REDIS_KERNEL_NAME = re.compile(r"^redis-server(?:$|[\s:])")
_SOCKET_FLAG = "unixsocket"

# ── the age arm: DERIVED from the cancel bound, never a remembered measurement ─
#
# This arm is a PROOF ("no running leg can own this server"), so its threshold
# has to clear the longest a leg can possibly stay alive — and "the widest leg
# watchdog" is not that length. Two repo facts settle the real bound:
#
#   1. A `test` leg's pytest watchdog is DERIVED, and the policy explicitly
#      permits it to reach 55 min: `tools/ci_selection.py::WATCHDOG_CEILING_MIN`,
#      emitted per leg by `fast_matrix_include()` and consumed as
#      `timeout -s INT -k 10 ${{ matrix.watchdog_minutes }}m`.
#   2. The HARD bound on a running leg is the job's OUTER `timeout-minutes` —
#      GitHub cancels the job there (workflow syntax, `jobs.<job_id>.timeout-
#      minutes`) — and that cap bounds SETUP + the last-failed pre-phase + the
#      gated run together, so it is strictly wider than any inner watchdog. An
#      UNCAPPED job is not "small but unbounded": it carries GitHub's
#      360-minute default, under which no age arm is a proof at all.
#
# The POPULATION this proof quantifies over is "jobs that can own an embedded
# redislite server", not "jobs that run this line" — the process table this
# tool reads is the HOST's, and ownership is decided by argv + age, never by
# which workflow started the server. Membership is read mechanically from the
# two signals a job needs in order to own one: it invokes this reaper, or it sets
# the URI-less embedded opt-in (`TORTOISE_TEST_CARVE_OUT`, read through the
# declared contract in `tests/_embedded.py`) — the marker, not the string
# "pytest", because a job may run pytest from INSIDE a shell script
# (`ci.yml::flip-gate` → `verify-cutover`), which no `pytest` text scan sees.
# The pin also carries an explicitly frozen expected set and two by-name
# exemptions for the requests-only e2e suites, so this population cannot drift
# silently in either direction.
#
# ⛔ WHAT THE SCAN CANNOT SEE, recorded rather than absorbed. Two residuals, both
# in the false-PASS direction, so neither may be quietly dropped:
#
#   a) A URI-set job can still keep an embedded server: `TEST_NO_REDIRECT_STEMS`
#      in `tests/_embedded.py` exempts a handful of files from the docker
#      redirect precisely so they KEEP constructing embedded (that module's own
#      comment: "Without this exemption a URI-set session redirects the
#      fixture's explicit `db_path` construction to the server"). A job that runs
#      one of those stems with `TORTOISE_DB_URI` set, no carve-out and no reaper
#      step is admitted by NEITHER signal. No workflow→file mapping is derivable
#      here — the shard split is computed by `tools/ci_selection.py` at run time —
#      so this is stated, not scanned: today those stems run inside
#      `python-ci.yml::test`/`test-slow`, which the reaper half admits and which
#      are capped below this arm. A job that starts running them elsewhere has to
#      add itself to the pin's population by hand.
#   b) The population is CI JOBS; `classify()` quantifies over EVERY `redis-server`
#      on the host, and the runners are shared with the agent fleet
#      (`python-ci.yml`: "jobs landing on local hardware compete with the agent
#      fleet"). A fleet `pytest` holding a session-scoped server for longer than
#      this arm has no `timeout-minutes` at all, so no workflow-derived arm can
#      prove anything about it. Pre-existing, not introduced here (the shipped 45
#      was strictly worse), but it is why this arm is a bound on OUR legs and not
#      a licence to reap anything older.
#
# At the time of writing the widest is `test` at 90 (`python-ci.yml`);
# `evals-on-demand.yml::evals` 90, `test-slow` 60,
# `post-merge-validation.yml::validate` 60, `perf-lane` 45, `test-d14-hosted-api`
# 50, `test-carve-out` 30, `ci.yml::flip-gate` 30 (capped by #10327's review —
# it was UNCAPPED at 360 and invisible to an earlier, narrower population),
# `test-record-postgres` 20 (raised to 50 on 2026-11-05 for a post-step cache
# save, then put BACK to 20 when #11063 made that job restore-only and removed
# the save the raise was derived from), `legal-e2e`/`blog-write-e2e` 20,
# and the rest 15.
#
# A server YOUNGER than the widest outer cancel bound can still belong to a live
# leg, so reaping on age is only a proof ABOVE it. The invariant is
#
#     DEFAULT_MAX_AGE_MINUTES > widest job `timeout-minutes` (>= the 55 ceiling)
#
# and both halves are pinned against their live sources by
# `tests/test_reap_redislite_orphans.py::test_the_age_arm_clears_every_embedded_lane_job_outer_timeout`
# and `::test_the_age_arm_clears_the_widest_permitted_leg_watchdog`. Raising the
# ceiling, or a job's outer cap, reds this file's own suite — instead of
# silently turning the proof back into a live-peer kill (the #3653 defect).
#
# NOT the same question as the 30-minute dir-litter floor the same step gives
# `find`, and NOT a safe floor to borrow: that arm deletes by mtime ALONE, with
# no liveness check at all, and its 30 minutes sit BELOW both the 55-minute
# watchdog ceiling and the 90-minute job cap this arm is derived from — so it
# CAN unlink a running peer's socket directory (redislite's `mkdtemp` dir, whose
# mtime is set at instance start). That is not harmless litter: the unlinked
# socket is exactly the "socket dir gone" input the first arm below reaps on.
# It is #3653's REMAINING HALF — the issue's own "Candidates for the deleter"
# names this `find`, and its "Suggested direction" asks for the pid/socket
# liveness check that would make the floor safe. Closing it is a behavioural
# change and is deliberately not attempted here. This ARM is not lowered toward
# that floor for the reason it is not lowered at all: a conservative arm costs a
# leak the orphan gate reports; a too-low arm costs somebody else's red PR.
#
# Mirrored as literals rather than imported: this script is deliberately
# stdlib-only and 3.9-clean, and it runs in a setup step under the runner's
# `python3` before the project venv or `tools/` is importable. The pins above
# are what keep the mirror honest.
WIDEST_EMBEDDED_JOB_TIMEOUT_MINUTES = 90
# Slack over the JOB bound — NOT over the leg's in-step watchdog. A job
# cancelled at its `timeout-minutes` is not gone at that instant: the runner
# cancels it, then tears its container down, and the server this tool can meet
# belongs to a DIFFERENT job on the same host, so that teardown can still be in
# flight while the reaper looks. 5 minutes covers that gap, and it is deliberately
# small because the bound it is added to already carries the margin.
# (The `timeout -s INT -k 10 …` in each leg's `run:` is the INNER pytest
# watchdog: it stops pytest, never the job, so it is not the clock this number
# is measured against.)
CANCEL_SLACK_MINUTES = 5
DEFAULT_MAX_AGE_MINUTES = WIDEST_EMBEDDED_JOB_TIMEOUT_MINUTES + CANCEL_SLACK_MINUTES


def _socket_dir_from_tokens(tokens: list[str]) -> str | None:
    """The socket directory from an already-tokenised command line."""
    for i, arg in enumerate(tokens):
        if not arg:
            continue
        if arg.startswith(_SOCKET_FLAG + ":"):
            path = arg.split(":", 1)[1].strip()
            if path:
                return os.path.dirname(path) or None
        elif arg in ("--" + _SOCKET_FLAG, _SOCKET_FLAG) and i + 1 < len(tokens):
            path = tokens[i + 1].strip()
            if path:
                return os.path.dirname(path) or None
        elif arg.endswith(".config"):
            return os.path.dirname(arg) or None
    return None


def socket_dir_from_argv(argv: list[str]) -> str | None:
    """The directory a redislite instance serves its socket out of, or None.

    Covers all three argv shapes redislite uses: the `unixsocket:<path>` pair
    inside its generated config, a bare `--unixsocket <path>`, and the
    config-file form (`redis-server <dbdir>/redis.config …`) used on macOS.
    An argv this function cannot read yields None and the caller keeps the
    process — unreadable is never authority to kill.

    Now also covers the shape a WRAPPER produces, where the command line is not
    aligned with argv boundaries at all: the whole invocation in ONE element
    (`argc == 1`, see `_split_one_arg_line`), or a launcher's string carrying it
    (`sh -c 'exec <bin>/redis-server unixsocket:…'`). That retry runs ONLY when
    the direct pass has already found nothing, so every shape that pass reads is
    unaffected — including a socket path that itself contains a space, which the
    direct pass still returns whole.
    """
    found = _socket_dir_from_tokens(argv)
    if found is not None:
        return found
    for arg in argv:
        if _has_whitespace(arg):
            found = _socket_dir_from_tokens(arg.split())
            if found is not None:
                return found
    return None


def _read_proc_uptime() -> float | None:
    try:
        with open("/proc/uptime") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _age_from_proc(pid: int) -> float | None:
    """Seconds since the process started, from /proc/<pid>/stat field 22."""
    uptime = _read_proc_uptime()
    if uptime is None:
        return None
    try:
        # `errors="replace"`: field 2 is `comm`, which any process can set to
        # arbitrary non-UTF-8 bytes (`prctl(PR_SET_NAME)`). A decode failure here
        # would abort the whole run — exit 1, nothing classified — which is the
        # "cleanup that fails is a leak" outcome this file exists to avoid. The
        # age is then simply unmeasurable, and an unmeasurable age is KEPT.
        with open(f"/proc/{pid}/stat", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return None
    # `comm` is parenthesised and may contain spaces, so split on the LAST ')'.
    try:
        fields = raw[raw.rindex(")") + 2 :].split()
        start_ticks = int(fields[19])  # field 22 overall; 20th after comm+state
        hz = os.sysconf("SC_CLK_TCK")
    except (ValueError, IndexError, OSError):
        return None
    if hz <= 0:
        return None
    age = uptime - (start_ticks / float(hz))
    return age if age >= 0 else None


def _age_from_ps(pid: int) -> float | None:
    """Linux `etimes` fallback; None when unavailable (macOS, ps missing)."""
    import subprocess

    try:
        out = subprocess.run(
            ["ps", "-o", "etimes=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
        return float(out) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _process_age(pid: int) -> float | None:
    age = _age_from_proc(pid)
    return age if age is not None else _age_from_ps(pid)


def _has_whitespace(token: str) -> bool:
    return any(ch.isspace() for ch in token)


def _split_one_arg_line(argv: list[str]) -> list[str]:
    """`argv` as the server itself parses it, for the `argc == 1` shape (#10502).

    A launcher that hands the kernel ONE argv element carrying the whole command
    line — `<bin>/redis-server unixsocket:<dir>/redis.socket` — leaves
    `_IS_REDIS.search(argv[0])` with no match, because the regex is anchored at
    END of string and the element ends in the socket path. The runner produces
    exactly that shape (measured on `pi-runner-x64-2`), so the single element is
    split on whitespace before anything reads it. A multi-element argv is
    returned untouched: there, argv boundaries are already the truth.
    """
    if len(argv) == 1 and _has_whitespace(argv[0]):
        return argv[0].split()
    return argv


def _read_proc_cmdline(pid: int) -> list[str] | None:
    """`argv` for `pid`, or None when `/proc/<pid>/cmdline` cannot be read.

    None (unreadable) is distinct from `[]` (empty command line — a zombie or a
    kernel thread) so the READER records which happened: the first may be a
    process that vanished mid-scan, the second is a process that HAS no argv to
    read a socket out of. ⛔ That distinction is in the return value only — every
    consumer today takes the same `if not argv:` KEEP arm for both, so nothing
    branches on it (PR #14182 review, note 2: the docstring is not a claim about
    downstream behaviour).
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    return [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]


def _read_proc_name(pid: int) -> str | None:
    """The KERNEL's name for the process (`/proc/<pid>/comm`), or None.

    Set at `exec` from the executed file's name, so a wrapper cannot disguise
    it from its own argv — the property #10502 needs. `comm` is world-readable,
    so a missing read means the process is gone, not that it is foreign.

    `errors="replace"` is load-bearing, not cosmetic: ANY process on the host
    can set its own name to non-UTF-8 bytes (`prctl(PR_SET_NAME)`) and an
    unprivileged user can do it to break a cleanup tool. Without it a
    `UnicodeDecodeError` — a `ValueError`, not an `OSError` — escapes the scan
    and the run dies mid-inventory: exit 1, no census line, nothing reaped, and
    (under the shipped `… || true` call sites) a GREEN step. A name we cannot
    decode is simply a name that is not `redis-server`.
    """
    try:
        with open(f"/proc/{pid}/comm", errors="replace") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _read_proc_exe_name(pid: int) -> str | None:
    """Basename of `/proc/<pid>/exe`, or None (gone, or not ours to read)."""
    try:
        return os.path.basename(os.readlink(f"/proc/{pid}/exe")) or None
    except OSError:
        return None


def _redis_identity(pid: int, argv: list[str] | None, comm: str | None = None) -> str | None:
    """HOW this pid is known to be a redis server, or None for "not one".

    Three sources, in cost order, and the LAST two are the authority:

    * `argv[0]` — the cheap fast path, and enough for a server started directly
      (and for any process that renames itself in place, where `comm` is no
      longer `redis-server` while `argv[0]` still is);
    * `/proc/<pid>/comm` — the kernel's own name, which a wrapper-exec'd server
      carries and the wrapper cannot set;
    * `/proc/<pid>/exe` — the resolved executable, for the corner where `comm`
      was renamed by `prctl` but the binary on disk is still `redis-server`.

    ⛔ NOT "any argv element names `redis-server`". A process that merely NAMES
    the binary in its arguments is not the server — it is a client, a test
    helper, a launcher whose child is the server — and reaping it on that
    evidence would be the live-peer kill #3653 removed. Identity is a fact about
    the EXECUTED binary, never about a string in someone's arguments.

    `comm` may be passed in when the caller has already read it (the scan reads
    it once per pid for the CENSUS, before identity is decided).
    """
    if argv and _IS_REDIS.search(argv[0]):
        return "argv[0]"
    name = comm if comm is not None else _read_proc_name(pid)
    if name is not None and _REDIS_KERNEL_NAME.search(name):
        return "comm"
    exe = _read_proc_exe_name(pid)
    if exe is not None and _REDIS_KERNEL_NAME.search(os.path.basename(exe)):
        return "exe"
    return None


class ProcInventory(NamedTuple):
    """What one pass over the process table measured.

    `census` is NOT derived from the rows, and deliberately not from
    `_redis_identity()`'s verdict either: it counts every process the KERNEL
    names `redis-server` (read straight out of `/proc/<pid>/comm`, or the `ps`
    `comm` field), BEFORE any candidate filter runs. That is what makes the gap
    signal in `main()` a real check instead of a restatement of the filter: if
    the identity gate stops matching — the #10502 edit — `census` stays non-zero
    and `census_accounted` collapses to 0.

    `census_accounted` / `census_excluded` count CENSUS members only: one per
    row, and one per stated `no unix socket in argv` exclusion. A row identified
    by `argv[0]` exists outside this arithmetic and is counted in `rows` alone.
    """

    rows: list[dict]
    census: int
    census_accounted: int
    census_excluded: int
    source: str

    @property
    def unaccounted(self) -> int:
        """Census members that are neither a candidate nor a stated exclusion.

        Structurally 0 on a healthy tree — every census member becomes a row or
        a counted exclusion — which is exactly why it is asserted: the edit that
        produced #10502 sits between "this IS a redis server" and "therefore
        here is its row", and that edit must RED (exit 3), not silently restore
        a measured zero on a host it never saw.
        """
        return max(0, self.census - self.census_accounted - self.census_excluded)


def _iter_proc_inventory() -> ProcInventory | None:
    """Every redislite candidate carrying a unix socket, with its age.

    Linux reads /proc directly (no subprocess, no shell, no glob). Anywhere
    else it shells out to `ps -eo pid=,comm=,args=`. The process is IDENTIFIED
    by `_redis_identity` — `argv[0]` first, then the kernel's `comm`, then the
    resolved exe — never by `argv[0]` alone (#10502, see the module docstring).

    A row whose argv cannot be read is still returned — with `socket_dir` None —
    so the caller can report it rather than silently dropping it. Excluding a
    process from the candidate list is only ever for a STATED reason: the
    `no unix socket in argv` arm (a plain TCP `redis-server` is not redislite
    litter), counted in `census_excluded` so the census reconciles.

    `None` — not an empty inventory — when the inventory could not be read AT
    ALL: /proc unlistable, or absent with `ps` unavailable or exiting non-zero.
    The distinction is the documented exit 2, and it is the difference between
    "measured the host and found no litter" and "never saw the host":
    returning `[]` for both printed `0 candidate(s), 0 reaped` and exited 0, so
    the tool's one failure signal could not fire where it was meant to
    (PR #10254 review, round 13). A single unreadable `/proc/<pid>/cmdline` is
    NOT this case — that pid either vanished mid-scan or is not readable, and it
    comes back as a row with `socket_dir` None, i.e. KEPT and reported (the safe
    direction), never as an empty inventory.
    """
    rows: list[dict] = []
    census = 0
    census_accounted = 0
    census_excluded = 0
    proc = os.path.isdir("/proc/self")
    if proc:
        try:
            entries = sorted(os.listdir("/proc"))
        except OSError:
            return None
        for entry in entries:
            if not entry.isdigit():
                continue
            pid = int(entry)
            # ONE read of the kernel's name, and the CENSUS is taken from it
            # before any filter: the count of processes named `redis-server`
            # must not depend on the code that decides whether to enumerate one
            # (that dependence is what made the pre-fix zero unverifiable).
            comm = _read_proc_name(pid)
            is_census = bool(comm is not None and _REDIS_KERNEL_NAME.search(comm))
            if is_census:
                census += 1
            argv = _read_proc_cmdline(pid)
            if argv is not None:
                argv = _split_one_arg_line(argv)
            source = _redis_identity(pid, argv, comm)
            if source is None:
                # A census member can only land here if the identity gate has
                # stopped matching the kernel's name — it stays UNACCOUNTED, and
                # `main()` exits 3 on it. That is the #10502 tripwire firing.
                continue
            if not argv:
                # Identified as a server, but there is no argv to read a socket
                # out of (gone mid-scan, unreadable, or a zombie): KEEP it and
                # REPORT it, which is this module's documented contract. It is a
                # row — not a silent drop — so the census keeps reconciling.
                rows.append(
                    {
                        "pid": pid,
                        "argv": [],
                        "socket_dir": None,
                        "age_seconds": _process_age(pid),
                        "identified_by": source,
                    }
                )
                census_accounted += int(is_census)
                continue
            if not any(_SOCKET_FLAG in a for a in argv):
                # A redis server with no unix socket is not redislite litter and
                # is none of this tool's business. Stated, counted, never silent.
                census_excluded += int(is_census)
                continue
            rows.append(
                {
                    "pid": pid,
                    "argv": argv,
                    "socket_dir": socket_dir_from_argv(argv),
                    "age_seconds": _process_age(pid),
                    "identified_by": source,
                }
            )
            census_accounted += int(is_census)
        return ProcInventory(rows, census, census_accounted, census_excluded, "/proc")

    import subprocess

    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,comm=,args="],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        # `ps` RAN and FAILED — denied, a broken binary, an unsupported `-eo`.
        # Its empty stdout is not a measurement, and this is the exact case the
        # None/exit-2 distinction exists for: a host that was never read must
        # not report `0 candidate(s)` as if it had been (PR #10254 review, P3).
        # `comm=` is part of that same contract: the pre-fix `-eo pid=,args=`
        # shipped the #10502 defect on this branch too, since `args` is argv and
        # a wrapper's argv names the wrapper. A `ps` that does not know `comm=`
        # exits non-zero here, which is exit 2 — "never saw the host" — instead
        # of a silent zero.
        return None
    out = result.stdout
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # `ps` joins argv with spaces and prints `comm` as a single field. A
        # process that renamed itself with a space in the name is unparseable
        # here and falls through to the argv path exactly as before; that can
        # only make the census SMALLER, so it can never invent a gap signal.
        pid_str, _, rest = line.partition(" ")
        if not pid_str.isdigit():
            continue
        comm, _, args = rest.strip().partition(" ")
        argv = args.split()
        # Same rule as the /proc branch: the CENSUS is taken from the kernel's
        # name before any candidate filter, so it cannot inherit a filter's zero.
        comm_name = os.path.basename(comm)
        is_census = bool(_REDIS_KERNEL_NAME.search(comm_name))
        if is_census:
            census += 1
        source = None
        if argv and _IS_REDIS.search(argv[0]):
            source = "argv[0]"
        elif is_census:
            source = "comm"
        if source is None:
            continue
        pid = int(pid_str)
        if not argv:
            rows.append(
                {
                    "pid": pid,
                    "argv": [],
                    "socket_dir": None,
                    "age_seconds": _process_age(pid),
                    "identified_by": source,
                }
            )
            census_accounted += int(is_census)
            continue
        if not any(_SOCKET_FLAG in a for a in argv):
            census_excluded += int(is_census)
            continue
        rows.append(
            {
                "pid": pid,
                "argv": argv,
                "socket_dir": socket_dir_from_argv(argv),
                "age_seconds": _process_age(pid),
                "identified_by": source,
            }
        )
        census_accounted += int(is_census)
    return ProcInventory(rows, census, census_accounted, census_excluded, "ps")


def classify(row: dict, max_age_s: float) -> tuple[bool, str]:
    """(should_reap, reason) for one inventory row — the whole decision table.

    Two arms may reap, and each is a PROOF that no running leg can own this
    server. Every other case keeps the process, including every case this
    function cannot measure: an unreadable age on a live socket is not evidence
    of abandonment, and killing on it is the exact defect #3653 records.
    """
    socket_dir = row.get("socket_dir")
    if socket_dir is None:
        return False, "argv names no socket directory — ownership undeterminable"
    if not os.path.isdir(socket_dir):
        # The running instance keeps its (now unlinked) socket; nobody can
        # reach it. This is the #1005 residue, and it needs no age argument.
        return True, f"socket dir {socket_dir} is gone — the instance can serve nothing"
    age = row.get("age_seconds")
    if age is None:
        return False, f"age unmeasurable and {socket_dir} still exists — fail closed"
    if age >= max_age_s:
        return True, (
            f"age {int(age)}s >= {int(max_age_s)}s — older than every leg's outer "
            "cancel bound, so no running leg can own it"
        )
    return False, (
        f"age {int(age)}s < {int(max_age_s)}s — could be a concurrent leg's live instance"
    )


def run(rows: list[dict], max_age_s: float, apply: bool) -> int:
    reaped = 0
    for row in rows:
        reap, reason = classify(row, max_age_s)
        tag = "REAP" if reap else "KEEP"
        print(f"{tag} pid={row.get('pid')}  {reason}")
        if not reap or not apply:
            continue
        try:
            os.kill(int(row["pid"]), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError) as exc:
            print(f"  could not signal pid={row.get('pid')}: {exc}")
            continue
        reaped += 1
    verb = "applied" if apply else "dry-run"
    print(f"{verb}: {len(rows)} candidate(s), {reaped} reaped")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="actually signal; without it this is a dry run"
    )
    parser.add_argument(
        "--max-age-minutes",
        type=float,
        default=DEFAULT_MAX_AGE_MINUTES,
        help="reap servers at least this old (default: %(default)s)",
    )
    parser.add_argument(
        "--proc-json",
        default=None,
        help="read the process inventory from this JSON file instead "
        "of the host (test seam; same schema the tool prints)",
    )
    args = parser.parse_args(argv)

    if args.proc_json:
        try:
            with open(args.proc_json) as fh:
                rows = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"could not read --proc-json {args.proc_json}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(rows, list):
            print("--proc-json must hold a JSON list", file=sys.stderr)
            return 2
        # No census: this inventory was HANDED to the tool, not measured by it.
        # Say so, so the run does not read as a host measurement either.
        print("census: not measured (inventory read from --proc-json)")
    else:
        inventory = _iter_proc_inventory()
        if inventory is None:
            # Exit 2 is the ONE documented "could not read the host" signal, so
            # "0 candidate(s)" is never a claim this run cannot make. Reported on
            # stderr and kept distinct from "measured, found nothing" (exit 0).
            print(
                "could not read the process inventory at all (neither /proc nor `ps`) — "
                "nothing was measured, so nothing was reaped",
                file=sys.stderr,
            )
            return 2
        rows = inventory.rows
        # The pre-filter population, printed EVERY run: this is the line that makes
        # `0 candidate(s)` readable, and it is what `ps -eo comm` saw on the host
        # where this tool reported a clean sweep over 1,537 orphans (#10502).
        print(
            f"census: {inventory.census} redis-server process(es) by kernel name "
            f"via {inventory.source}; {inventory.census_accounted} accounted for as "
            f"candidate(s); {inventory.census_excluded} excluded: no unix socket in "
            f"argv; {inventory.unaccounted} not accounted for"
        )
        if inventory.unaccounted:
            print(
                f"census gap: {inventory.unaccounted} process(es) are NAMED "
                "redis-server but were neither enumerated nor excluded — this run did "
                "not see the population it reports on, so its candidate count is a "
                "parse/filter gap and NOT a measured zero (#10502)",
                file=sys.stderr,
            )
            return 3

    return run(rows, args.max_age_minutes * 60.0, args.apply)


if __name__ == "__main__":
    sys.exit(main())
