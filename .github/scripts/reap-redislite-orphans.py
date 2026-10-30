#!/usr/bin/env python3
"""Reap redislite servers that are PROVABLY orphaned — never a live peer's (#3653).

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
    python3 .github/scripts/reap-redislite-orphans.py --max-age-minutes 120 --apply

Exit codes: 0 = ran (dry or apply) — INCLUDING when it found nothing to look at;
2 = something this tool was told to read could not be read (`--proc-json`, or the
process inventory itself: neither `/proc` nor `ps` answered — `ps` missing AND
`ps` exiting non-zero are the same fact — so NOTHING was measured and nothing was
reaped). Every other unmeasurable case is per-row and KEEPS its process with a
printed reason, which is still exit 0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys

# The redislite signature: the redis-server binary plus a unix socket in argv.
_IS_REDIS = re.compile(r"(?:^|/)(redis-server)$")
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
# two signals a workflow cannot own a server without: it invokes this reaper,
# or it sets the URI-less embedded opt-in (`TORTOISE_TEST_CARVE_OUT`, read
# through the declared contract in `tests/_embedded.py`) — the marker, not the
# string "pytest", because a job may run pytest from INSIDE a shell script
# (`ci.yml::flip-gate` → `verify-cutover`), which no `pytest` text scan sees.
# The pin also carries an explicitly frozen expected set and two by-name
# exemptions for the requests-only e2e suites, so this population cannot drift
# silently in either direction.
#
# At the time of writing the widest is `test` at 90 (`python-ci.yml`);
# `evals-on-demand.yml::evals` 90, `test-slow` 60,
# `post-merge-validation.yml::validate` 60, `perf-lane` 45, `test-d14-hosted-api`
# 40, `test-carve-out` 30, `ci.yml::flip-gate` 30 (capped by #10327's review —
# it was UNCAPPED at 360 and invisible to an earlier, narrower population),
# `test-record-postgres`/`legal-e2e`/`blog-write-e2e` 20, and the rest 15.
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
# `timeout -s INT -k 10` gives a cancelled leg 10 s after SIGINT before SIGKILL,
# and the runner then tears the step down. Minutes of slack over the bound,
# deliberately small: the bound itself already carries the margin.
CANCEL_SLACK_MINUTES = 5
DEFAULT_MAX_AGE_MINUTES = WIDEST_EMBEDDED_JOB_TIMEOUT_MINUTES + CANCEL_SLACK_MINUTES


def socket_dir_from_argv(argv: list[str]) -> str | None:
    """The directory a redislite instance serves its socket out of, or None.

    Covers all three argv shapes redislite uses: the `unixsocket:<path>` pair
    inside its generated config, a bare `--unixsocket <path>`, and the
    config-file form (`redis-server <dbdir>/redis.config …`) used on macOS.
    An argv this function cannot read yields None and the caller keeps the
    process — unreadable is never authority to kill.
    """
    for i, arg in enumerate(argv):
        if not arg:
            continue
        if arg.startswith(_SOCKET_FLAG + ":"):
            path = arg.split(":", 1)[1].strip()
            if path:
                return os.path.dirname(path) or None
        elif arg in ("--" + _SOCKET_FLAG, _SOCKET_FLAG) and i + 1 < len(argv):
            path = argv[i + 1].strip()
            if path:
                return os.path.dirname(path) or None
        elif arg.endswith(".config"):
            return os.path.dirname(arg) or None
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
        with open(f"/proc/{pid}/stat") as fh:
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


def _iter_proc_inventory() -> list[dict] | None:
    """Every redis-server carrying a unix socket in argv, with its age.

    Linux reads /proc directly (no subprocess, no shell, no glob). Anywhere
    else it shells out to `ps -eo pid=,args=`. A row whose argv cannot be read
    is still returned — with `socket_dir` None — so the caller can report it
    rather than silently dropping it.

    `None` — not `[]` — when the inventory could not be read AT ALL: /proc
    unlistable, or absent with `ps` unavailable or exiting non-zero. The
    distinction is the documented exit 2, and it is the difference between
    "measured the host and found no litter" and "never saw the host":
    returning `[]` for both printed
    `0 candidate(s), 0 reaped` and exited 0, so the tool's one failure signal
    could not fire where it was meant to (PR #10254 review, round 13). A single
    unreadable `/proc/<pid>/cmdline` is NOT this case — that pid simply
    vanished mid-scan, and skipping it keeps the process, which is the safe
    direction.
    """
    rows: list[dict] = []
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
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue
            argv = [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]
            if not argv or not _IS_REDIS.search(argv[0]):
                continue
            if not any(_SOCKET_FLAG in a for a in argv):
                continue
            rows.append(
                {
                    "pid": pid,
                    "argv": argv,
                    "socket_dir": socket_dir_from_argv(argv),
                    "age_seconds": _process_age(pid),
                }
            )
        return rows

    import subprocess

    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
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
        return None
    out = result.stdout
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_str, _, args = line.partition(" ")
        if not pid_str.isdigit():
            continue
        argv = args.split()
        if not argv or not _IS_REDIS.search(argv[0]):
            continue
        if not any(_SOCKET_FLAG in a for a in argv):
            continue
        pid = int(pid_str)
        rows.append(
            {
                "pid": pid,
                "argv": argv,
                "socket_dir": socket_dir_from_argv(argv),
                "age_seconds": _process_age(pid),
            }
        )
    return rows


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
    else:
        rows = _iter_proc_inventory()
        if rows is None:
            # Exit 2 is the ONE documented failure signal: the host was never read, so
            # "0 candidate(s)" would be a claim this run cannot make. Reported on
            # stderr and kept distinct from "measured, found nothing" (exit 0).
            print(
                "could not read the process inventory at all (neither /proc nor `ps`) — "
                "nothing was measured, so nothing was reaped",
                file=sys.stderr,
            )
            return 2

    return run(rows, args.max_age_minutes * 60.0, args.apply)


if __name__ == "__main__":
    sys.exit(main())
