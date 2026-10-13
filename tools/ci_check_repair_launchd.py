#!/usr/bin/env python3
"""launchd entry point for the #8375 item-1 local fallback.

WHY THIS FILE EXISTS — and why the plist does NOT simply run `bash` on the
launcher (this was a real defect in the first cut of the installer; the two
in-repo precedents below are what caught it):

macOS TCC is per-binary. It denies a launchd-spawned `bash` the read of a script
under `~/Documents` ("Operation not permitted"), and a launchd-spawned `git` the
read of a repository there ("not a git repository"). This launcher is under
`~/Documents` (the checkout) AND it runs `git -C <repo> rev-parse` at startup, so
a `bash <launcher>` entry point cannot work — the job would be installed, report
`loaded: yes`, and then never do anything. That is the "installed but silently
dead" class the installer's own pre-flight exists to prevent, and the pre-flight
cannot see it (an `-x` test follows the symlink and succeeds).

The binary that DOES hold the access is the checkout's own interpreter, which is
exactly what the two PROVEN launchd agents in this repo run:

  * `~/Library/LaunchAgents/com.tortoise.queue-reconcile.plist` → the checkout's
    `.venv/bin/python` on `tools/queue_reconcile.py`;
  * `com.tortoise.worktree-reaper.plist` → the checkout's interpreter on
    `pi-reap-worktrees-launchd.py`, whose header records the falsified control
    experiment verbatim ("a `bash <script>` entry point cannot work no matter
    where the script lives").

So launchd starts that interpreter here; THIS process performs the file read it
is allowed to do and hands the launcher to bash on stdin. `bash -s` never opens
the file itself, and the `git` it spawns inherits the access from this process.

DIFFERENCE FROM `pi-reap-worktrees-launchd.py` (deliberate): that one takes
`--script <path>` because its script is farmed out to `~/.pi/agent/scripts`. This
launcher is a SIBLING of this file, so the path is derived from `__file__` and
cannot be pointed at the wrong checkout by a stale argument. Everything else —
the read, the `bash -s` handoff, the exit-code pass-through — is the same shape.

USAGE (all arguments are forwarded to the launcher unchanged):
    ci_check_repair_launchd.py --apply
    ci_check_repair_launchd.py            # report-only
Exit code: the launcher's own (0 ok / 1 unresolved / 2 could-not-measure).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LAUNCHER = Path(__file__).resolve().with_name("ci-check-repair-local.sh")


def main(argv: list[str] | None = None) -> int:
    forward = list(sys.argv[1:] if argv is None else argv)
    try:
        with LAUNCHER.open("rb") as fh:
            # `bash -s` consumes the launcher from stdin, so bash never has to
            # open the file itself — that read is the one TCC denies it.
            #
            # `CI_REPAIR_SCRIPT_DIR` is NOT decoration: with `bash -s` there is no
            # script ARGUMENT, so `BASH_SOURCE[0]` is unset and the launcher (which
            # runs `set -u`) would fall back to $PWD — under the plist that is the
            # repo root, so `$SCRIPT_DIR/..` resolves to the repo's PARENT and the
            # tool is looked for at `<parent>/tools/...` and never found. The job
            # would install, report `loaded: yes`, and exit 2 on every single fire.
            env = {**os.environ, "CI_REPAIR_SCRIPT_DIR": str(LAUNCHER.parent)}
            return subprocess.run(["/bin/bash", "-s", "--", *forward],
                                  stdin=fh, env=env).returncode
    except OSError as exc:
        print(f"ci_check_repair_launchd: cannot read {LAUNCHER}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
