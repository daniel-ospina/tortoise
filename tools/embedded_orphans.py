#!/usr/bin/env python3
"""Bounded embedded-redislite orphan census (issue #3599).

The invariant this reports: **a lane must not leave orphaned embedded
redislite servers behind**. #3599 found 527 of them on one dev box (host
load 98 on 10 CPUs) because the reaper's only_safe mode could never confirm
an orphan while any other suite was running — an invariant nobody could
observe, so nobody noticed for a working day.

This is the observable form of that invariant. It is deliberately bounded:
the default path costs ONE `pgrep` plus one batched `ps` plus one
read-only unix-socket probe per server — no filesystem walk at all (the
tempdir on a leaky box holds tens of thousands of entries, and an unbounded
walk is itself a load spike — see the #1069 family). `--deep` opts into the
reaper's time-budgeted tempdir walk for stale-socket dirs.

"Orphan" here means a server the reaper would ACTUALLY reap, using the same
predicates: a live detached redislite server with no live owner record and
no client, or one whose socket dir is gone. This is deliberately NOT a bare
`pgrep -c redis-server` — that counts live servers, which is not the
invariant and would be satisfied by a machine running many healthy lanes.

Usage:
    python3 tools/embedded_orphans.py                 # human line, exit 1 if over budget
    python3 tools/embedded_orphans.py --json
    python3 tools/embedded_orphans.py --max-orphans 5
    python3 tools/embedded_orphans.py --deep

Exit codes: 0 = at or under the budget, 1 = over budget, 2 = census failed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_MAX_ORPHANS = 5


def _require_enumerable_pgrep() -> None:
    """Raise when server enumeration cannot be trusted (#3599 review).

    `tortoise.embedded_reaper._pgrep_redis_servers` swallows a missing OR
    TIMING-OUT `pgrep` and returns `[]`, which is indistinguishable from "no
    servers". A census on a host where pgrep times out — precisely the load
    level #3599 documents — would then print `orphans: 0` and exit 0 while
    orphans accumulate, i.e. the observability check would fail open.

    So probe the SAME command it runs: rc 0 (matches) and rc 1 (no matches)
    are both real answers; anything else, including a timeout, is not.
    """
    if shutil.which("pgrep") is None:
        raise RuntimeError(
            "pgrep is not available — the census cannot distinguish 'no "
            "servers' from 'could not enumerate'; refusing to report clean")
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "redislite/bin/redis-server"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(
            f"pgrep could not enumerate embedded servers ({exc!r}) — the "
            f"census cannot distinguish 'no servers' from 'could not "
            f"enumerate'; refusing to report clean") from exc
    if proc.returncode not in (0, 1):  # 0 = matches, 1 = no matches
        raise RuntimeError(
            f"pgrep exited {proc.returncode} — enumeration is unreliable; "
            f"refusing to report clean")


def census(*, deep: bool = False, jobs: int = 8) -> dict:
    """Classify every live embedded server; return the counts.

    Never raises for an individual server (per-record isolation, mirroring
    the reaper): an unclassifiable server is counted in `unclassified` and
    is never reported as an orphan (fail closed).

    But it does NOT fail closed on a census that could not RUN: if `pgrep`
    is missing *or times out* (which `_pgrep_redis_servers` reports as an
    empty list, indistinguishable from "no servers"), an exit 0 would
    report the invariant satisfied on a host where orphans may be
    accumulating. That is the exact fail-open the tool exists to catch, so
    it raises instead (and `main` maps that to exit 2).
    """
    _require_enumerable_pgrep()
    from tortoise.embedded_reaper import (
        _PROC_INFO_CACHE,
        _active_client_count,
        _batch_process_info,
        _classify_dir,
        _owner_records,
        _pgrep_redis_servers,
        _socket_dir_from_cmdline,
        _socket_dir_missing,
    )

    pids = _pgrep_redis_servers()
    _PROC_INFO_CACHE.update(_batch_process_info(pids))
    try:
        orphans: list[dict] = []
        protected = 0
        unclassified = 0
        for pid in pids:
            try:
                sock_dir = _socket_dir_from_cmdline(pid)
                if not sock_dir:
                    unclassified += 1
                    continue
                socket_path = f"{sock_dir}/redis.socket"
                rec = _classify_dir(sock_dir, socket_path, known_pid=pid)
                if rec is None:
                    unclassified += 1
                    continue
                if rec.get("classification") != "candidate":
                    protected += 1
                    continue
                owners = _owner_records(socket_path)
                no_live_owner = (owners is not None and owners[0] == 0
                                 and not rec.get("path_based"))
                dir_missing = bool(_socket_dir_missing(socket_path))
                # #3599 review cycle 2: an owner record is not the only way a
                # server can be busy — mirror reap()'s CLIENT LIST gate, or
                # the census reports a server that reap() would refuse (a
                # non-tortoise client, or a lost record). Fail closed: a
                # probe that cannot answer (None) with the dir present is
                # NOT evidence of orphanhood.
                if dir_missing:
                    reason = "socket-dir-missing"
                elif no_live_owner and _active_client_count(socket_path) == 0:
                    reason = "no-live-owner"
                else:
                    protected += 1
                    continue
                orphans.append({"pid": pid, "socket_path": socket_path,
                                "reason": reason})
            except Exception:  # per-record isolation — never fail the census
                unclassified += 1
        stale_dirs = []
        if deep:
            from tortoise.embedded_reaper import _find_socket_dirs, _registry_for
            for d in _find_socket_dirs(__import__("tempfile").gettempdir()):
                try:
                    reg = _registry_for(d)
                    if reg is None:
                        continue
                    pidfile = reg.get("pidfile")
                    if not pidfile or not Path(pidfile).exists():
                        stale_dirs.append(d)
                except Exception:
                    continue
    finally:
        _PROC_INFO_CACHE.clear()

    return {
        "live_servers": len(pids),
        "orphans": len(orphans),
        "orphan_details": orphans,
        "protected": protected,
        "unclassified": unclassified,
        "stale_socket_dirs": len(stale_dirs),
        "stale_socket_dir_sample": stale_dirs[:10],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="embedded_orphans",
        description="Bounded embedded-redislite orphan census (issue #3599).")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable output")
    parser.add_argument("--max-orphans", type=int, default=DEFAULT_MAX_ORPHANS,
                        help=f"Exit 1 when the orphan count exceeds this "
                             f"(default {DEFAULT_MAX_ORPHANS})")
    parser.add_argument("--deep", action="store_true",
                        help="Also run the reaper's time-budgeted tempdir "
                             "stale-socket walk (bounded, but not free)")
    parser.add_argument("--jobs", type=int, default=8,
                        help="Reserved for parity with the reaper (default 8)")
    args = parser.parse_args(argv)

    try:
        result = census(deep=args.deep, jobs=args.jobs)
    except Exception as exc:  # a census that cannot run must not read as clean
        print(f"embedded_orphans: census failed: {exc}", file=sys.stderr)
        return 2

    result["max_orphans"] = args.max_orphans
    result["within_budget"] = result["orphans"] <= args.max_orphans
    # A census whose every server was unclassifiable learned nothing about
    # the invariant — reporting exit 0 there is the fail-open this tool
    # exists to prevent (#3599 review).
    result["inconclusive"] = (result["live_servers"] > 0
                              and result["unclassified"]
                              == result["live_servers"])
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[embedded-orphans] live embedded servers: "
              f"{result['live_servers']} | orphans: {result['orphans']} "
              f"(budget {args.max_orphans}) | protected: "
              f"{result['protected']} | unclassified: "
              f"{result['unclassified']}"
              + (f" | stale socket dirs: {result['stale_socket_dirs']}"
                 if args.deep else ""))
        for o in result["orphan_details"][:20]:
            print(f"  orphan pid={o['pid']} reason={o['reason']} "
                  f"{o['socket_path']}")
    if result["inconclusive"]:
        print("embedded_orphans: INCONCLUSIVE — every live server failed "
              "classification; refusing to report the invariant satisfied",
              file=sys.stderr)
        return 2
    return 0 if result["within_budget"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
