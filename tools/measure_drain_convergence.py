#!/usr/bin/env python3
"""#14350 (D1 of #14342) — measure the drain's convergence, before and after.

The ceiling this change states (`DRAIN_MAX_IN_FLIGHT = 1`, one drainer per spool)
is justified by a MEASUREMENT, not by comfort — and this script is the
measurement. It reproduces the shape the handoff measured (§7.3: 908 entries,
``attempts`` 10–17, a live ``capture_capacity_saturated`` refusal, a backlog that
grew while it drained) at a smaller population, against a fake server that obeys
the REAL server gate — ``hosted_api._reserve_capture_slot``: 8 concurrent
captures per box, then 429 + ``retry_after``.

    python tools/measure_drain_convergence.py            # JSON receipt
    python tools/measure_drain_convergence.py --pretty

⛔ It has NO flag that disables the fix. "Before" is measured by running the SAME
script against a checkout at the parent commit (``git worktree add``), so the
comparison is between two trees, not between two modes of one tree — a script
with a `--legacy` switch measures its own switch, and a seam that can turn the
claim off is a seam an operator can reach in production.

What it measures, and why each number is in the receipt:

* ``pending_before`` / ``pending_after`` — the epic's condition 1 ("the backlog
  head reaches zero"). ``None`` means the spool could not be listed.
* ``max_attempts`` — the falsifier's second half ("no entry exceeding
  ``attempts = 3``"). It is the number the pre-fix drain drove to 10–17.
  ⛔ Read it as ``max_attempts_high_water``, sampled after EVERY round. Reading
  it only at the end is a FALSE PASS in the after-tree: filing an entry removes
  its meta, so a converged run reads 0 no matter what the saturated rounds
  charged. Both numbers are emitted; the high-water one is the falsifier's.
* ``refusals`` — how many ``capture_capacity_saturated`` answers the drain itself
  provoked. This is the self-feeding mechanism: the drain competing with itself
  for the box's eight slots.
* ``high_water`` — the most captures the DRAIN held concurrently. It is the
  ceiling's own measurement: at 1, seven of the server's eight slots stay free
  for the user's live captures (condition 3's half of the story).
* ``claim_winners`` — from a separate 8-process race: how many drainers acquired
  the claim. MUST be 1. Threads would do for the drain measurement (the claim is
  file-based, so it excludes them identically), but the population this protects
  is PROCESSES, so the exclusion is measured across processes.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

#: The server's own gate (`hosted_api._CAPTURE_MAX_IN_FLIGHT`).
SERVER_LIMIT = 8


def _seed_spool(root: Path, sessions: int) -> None:
    from tortoise.capture_spool import Snapshot, write_spool_entry

    for i in range(sessions):
        write_spool_entry(
            root,
            Snapshot(session_id=f"measured-{i:04d}",
                     turns=[{"role": "user", "content": f"turn {i}"}],
                     source="measure", machine_id="measure-box"),
        )


class CeilingServer:
    """The server's capture gate, and only that: a slot counter and a clock.

    ``saturated_until`` models the OTHER half of the measured box: LIVE captures
    holding the eight slots. While the wall clock is before it, every POST is
    refused for capacity (the drain is competing with the user's own traffic,
    which is what really happened on a box with several sessions open).

    ``retry_after`` is advertised in the body. The real server advertises 30 s
    (`hosted_api._CAPTURE_CAPACITY_RETRY_AFTER_S`); the measurement uses a
    fraction of a second, because the WAIT is scaled and the MECHANISM is not —
    the drain still honours the server's own number instead of inventing one.
    """

    def __init__(self, post_seconds: float = 0.002, saturated_until: float = 0.0,
                 retry_after: float = 0.05) -> None:
        self.post_seconds = post_seconds
        self.saturated_until = saturated_until
        self.retry_after = retry_after
        self.in_flight = 0
        self.high_water = 0
        self.refusals = 0
        self.filed = 0
        self._lock = threading.Lock()

    def _capacity_body(self) -> str:
        return json.dumps({
            "detail": {
                "code": "capture_capacity_saturated",
                "message": "capture capacity saturated",
                "retry_after": self.retry_after,
                "in_flight": SERVER_LIMIT,
                "limit": SERVER_LIMIT,
            }
        })

    def post(self, payload: dict):
        from tortoise.capture_spool import PostOutcome

        live = time.monotonic() < self.saturated_until
        with self._lock:
            self.in_flight += 1
            self.high_water = max(self.high_water, self.in_flight)
            saturated = live or self.in_flight > SERVER_LIMIT
            if saturated:
                self.refusals += 1
        try:
            if saturated:
                return PostOutcome(ok=False, status=429, detail=self._capacity_body())
            time.sleep(self.post_seconds)
            with self._lock:
                self.filed += 1
            return PostOutcome(ok=True, status=200,
                               body={"session_id": payload["session_id"],
                                     "extracted": 2})
        finally:
            with self._lock:
                self.in_flight -= 1


def _claim_race(processes: int) -> dict:
    """How many of N concurrent PROCESSES take the spool's claim? Must be 1.

    Skipped (``null``) on a tree that has no ``tortoise.drain_claim`` — i.e. the
    BEFORE measurement — because the honest answer there is "there is no claim",
    not "0 winners".
    """
    if not (REPO_ROOT / "tortoise" / "drain_claim.py").is_file():
        return {"supported": False, "winners": None}
    root = Path(tempfile.mkdtemp()) / "claim-race"
    root.mkdir(parents=True, exist_ok=True)
    arrivals = Path(tempfile.mkdtemp()) / "arrived"
    arrivals.mkdir(parents=True, exist_ok=True)
    done = Path(tempfile.mkdtemp()) / "done"
    done.mkdir(parents=True, exist_ok=True)
    # The WINNER KEEPS THE CLAIM while every loser gives up: a winner that exits
    # immediately leaves a claim with a dead pid, and the next process would
    # CORRECTLY reclaim it — counting that as a second winner would measure the
    # probe's own lifetime, not the exclusion.
    #
    # ⛔ AND IT WAITS FOR EVERY PEER TO HAVE *ARRIVED*, not for a fixed 1.0 s. A
    # fixed hold is a false-positive generator under load: measured here, importing
    # `tortoise.drain_claim` costs ~0.19 s per process on an idle box, and on an
    # oversubscribed cell ten CPython startups spread far past a one-second hold —
    # so a late starter legitimately took the claim the winner had already released
    # and was counted as a SECOND WINNER by a protocol that is correct. The count is
    # only meaningful over a population that provably overlapped (#14423 cycle-7
    # review, P2). Each process touches its own arrival marker BEFORE it attempts,
    # so `all-present` means every attempt happened while the winner held.
    script = (
        f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "import os, time\n"
        "from pathlib import Path\n"
        f"arrivals = Path({str(arrivals)!r})\n"
        f"done = Path({str(done)!r})\n"
        f"root = Path({str(root)!r})\n"
        "me = str(os.getpid())\n"
        "(arrivals / me).write_bytes(me.encode())\n"
        "deadline = time.monotonic() + 60.0\n"
        "while len(list(arrivals.iterdir())) < " + str(processes) + ":\n"
        "    if time.monotonic() > deadline:\n"
        "        print('BARREN')\n"
        "        raise SystemExit(3)\n"
        "    time.sleep(0.005)\n"
        "from tortoise.drain_claim import acquire\n"
        "c = acquire(root, leg='measure')\n"
        "if c is None:\n"
        "    (done / me).write_bytes(b'x')\n"
        "    print('LOST')\n"
        "    raise SystemExit(0)\n"
        "print('WON', flush=True)\n"
        "# hold until every peer has FINISHED its attempt\n"
        "while len(list(done.iterdir())) < " + str(processes - 1) + ":\n"
        "    if time.monotonic() > deadline:\n"
        "        print('WAITED_TOO_LONG')\n"
        "        break\n"
        "    time.sleep(0.005)\n"
        "c.release()\n"
        "(done / me).write_bytes(b'x')\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True)
             for _ in range(processes)]
    winners = 0
    for proc in procs:
        out, _err = proc.communicate(timeout=90)
        winners += out.count("WON")
    # `overlapped` is the count of processes that reached the attempt line while the
    # winner still held the claim — the population the answer is about. A run where
    # it is below `processes` proves the winner's hold was too short, so the number
    # is reported rather than silently trusted.
    # `overlapped` is how many processes both ANNOUNCED and FINISHED their attempt
    # while the winner held the claim — the population the answer is about. A run
    # where it is below `processes` proves the barrier did not hold, so the number
    # is reported rather than silently trusted.
    overlapped = min(len(list(arrivals.iterdir())), len(list(done.iterdir())) + winners)
    return {"supported": True, "winners": winners, "processes": processes,
            "overlapped": overlapped}


def measure(sessions: int, drainers: int, post_seconds: float) -> dict:
    from tortoise.capture_spool import (
        count_pending_captures,
        flush_spool,
        read_spool_meta,
    )

    root = Path(tempfile.mkdtemp()) / "spool"
    _seed_spool(root, sessions)
    pending_before = count_pending_captures(root)
    server = CeilingServer(post_seconds)
    summaries: list[dict] = []
    lock = threading.Lock()

    def drainer() -> None:
        started = time.monotonic()
        summary = flush_spool(root, server.post, now=time.time() * 1000.0)
        with lock:
            summaries.append({
                "filed": summary.filed,
                "deferred": summary.deferred,
                "attempted": summary.attempted,
                "held_by_claim": bool(getattr(summary, "held_by_claim", False)),
                "capacity_stopped": bool(getattr(summary, "capacity_stopped", False)),
                "seconds": round(time.monotonic() - started, 3),
            })

    started = time.monotonic()
    threads = [threading.Thread(target=drainer) for _ in range(drainers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=300)
    elapsed = round(time.monotonic() - started, 3)

    metas = [read_spool_meta(root, f"measured-{i:04d}") or {} for i in range(sessions)]
    attempts = [meta.get("attempts", 0) for meta in metas]
    return {
        "sessions": sessions,
        "drainers": drainers,
        "server_limit": SERVER_LIMIT,
        "post_seconds": post_seconds,
        "pending_before": pending_before,
        "pending_after": count_pending_captures(root),
        # ONE pass here, so the high-water and after-filing readings coincide; the
        # saturated receipt is where they diverge and where only the high-water one
        # is a falsifier reading.
        "max_attempts_high_water": max(attempts) if attempts else 0,
        "max_attempts_after_filing": max(attempts) if attempts else 0,
        "attempts_histogram": {str(n): attempts.count(n) for n in sorted(set(attempts))},
        "attempted": sum(p.get("attempted", 0) for p in summaries),
        "filed": server.filed,
        "refusals": server.refusals,
        "high_water": server.high_water,
        "elapsed_s": elapsed,
        "passes": summaries,
        "claim_race": _claim_race(drainers),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=60,
                        help="spool entries (the handoff's 908, scaled down)")
    parser.add_argument("--drainers", type=int, default=10,
                        help="concurrent drains (the measured shape: every "
                             "SessionStart fires one)")
    parser.add_argument("--post-seconds", type=float, default=0.002)
    parser.add_argument("--scenario", choices=("baseline", "saturated", "all"),
                        default="baseline",
                        help="baseline: 10 concurrent drains, free server; "
                             "saturated: the box's slots held by live captures, "
                             "three passes, then one free pass")
    parser.add_argument("--rounds", type=int, default=12,
                        help="saturated passes before the slots free; see the "
                             "docstring's arithmetic for why the default is 12")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    if args.scenario == "saturated":
        receipt = measure_saturated(args.sessions, args.drainers, args.rounds,
                                    post_seconds=args.post_seconds)
    elif args.scenario == "all":
        receipt = {
            "baseline": measure(args.sessions, args.drainers, args.post_seconds),
            "saturated": measure_saturated(args.sessions, args.drainers, args.rounds,
                                           post_seconds=args.post_seconds),
        }
    else:
        receipt = measure(args.sessions, args.drainers, args.post_seconds)
    receipt["tree"] = str(REPO_ROOT)
    print(json.dumps(receipt, indent=2 if args.pretty else None, sort_keys=True))
    return 0




def measure_saturated(sessions: int, drainers: int, rounds: int = 12,
                      advance_s: float = 35.0, post_seconds: float = 0.002) -> dict:
    """The MEASURED shape: a box whose captures are live, so the drain is refused.

    ``rounds`` passes are run while the server's eight slots are held by the
    user's own captures (every drain POST is a `capture_capacity_saturated` 429),
    with the backoff clock advanced ``advance_s`` between rounds — the cadence of
    successive `SessionStart` opportunities, so an armed 30 s window does not
    simply throttle the run to real time. Then the slots free and ONE more pass
    runs, which is the question the epic asks: does the backlog recover?

    ``attempts`` is the falsifier's number. The pre-#14350 drain charges every
    entry for the BOX's refusal on every pass, and `backoff_delay` is exponential
    (30 s → 6 h), so the entries are pushed out of reach of the pass that could
    actually file them. **The arithmetic is what the default ``rounds`` encodes**:
    at ``advance_s`` = 35 s per round the backoff windows are what matter, not the
    number of rounds — after round 1 (max attempts 1) the window is 30 s and round 2
    (70 s later) clears it; after round 2 (attempts 2) the window is 60 s and round
    4 (140 s) clears it; only from attempts 3 does the window (120 s) outlive the
    default run. A 3-round default therefore STOPS BEFORE the pre-fix drain fails,
    and measured both trees as converged — a false "before" (review finding). 12
    rounds drives attempts to ~10 (the handoff's own 10–17) in the before-tree,
    where the last pass cannot file anything at all.

    ⛔ ``max_attempts_high_water`` is the falsifier reading; ``max_attempts`` after
    the free pass is NOT (filing clears the meta, so it reads 0 in the after-tree
    and would "pass" even if the saturated rounds had charged every entry).
    """
    from tortoise.capture_spool import (
        count_pending_captures,
        flush_spool,
        read_spool_meta,
    )

    root = Path(tempfile.mkdtemp()) / "spool"
    _seed_spool(root, sessions)
    pending_before = count_pending_captures(root)
    server = CeilingServer(post_seconds)
    rounds_out: list[dict] = []
    high_water = 0
    base_ms = time.time() * 1000.0

    def run_round(index: int) -> list[dict]:
        summaries: list[dict] = []
        lock = threading.Lock()
        now_ms = base_ms + index * advance_s * 1000.0

        def drainer() -> None:
            summary = flush_spool(root, server.post, now=now_ms)
            with lock:
                summaries.append({
                    "filed": summary.filed,
                    "deferred": summary.deferred,
                    "attempted": summary.attempted,
                    "held_by_backoff": summary.held_by_backoff,
                    "held_by_claim": bool(getattr(summary, "held_by_claim", False)),
                    "capacity_stopped": bool(getattr(summary, "capacity_stopped", False)),
                })

        threads = [threading.Thread(target=drainer) for _ in range(drainers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=300)
        return summaries

    # The saturated passes: the user's captures hold all eight slots.
    server.saturated_until = time.monotonic() + 3600.0
    for index in range(rounds):
        passes = run_round(index)
        attempts = max((read_spool_meta(root, f"measured-{i:04d}") or {}).get("attempts", 0)
                       for i in range(sessions))
        high_water = max(high_water, attempts)
        rounds_out.append({
            "round": index + 1,
            "server": "saturated (live captures hold all 8 slots)",
            "pending": count_pending_captures(root),
            "max_attempts": attempts,
            "passes": passes,
        })

    # The slots free: this is the pass that must converge.
    server.saturated_until = 0.0
    passes = run_round(rounds)
    metas = [read_spool_meta(root, f"measured-{i:04d}") or {} for i in range(sessions)]
    attempts = [meta.get("attempts", 0) for meta in metas]
    windows = [meta.get("next_attempt_at_ms", 0) for meta in metas]
    final_now = base_ms + rounds * advance_s * 1000.0
    blocked_by_backoff = sum(1 for w in windows if w and w > final_now)
    rounds_out.append({
        "round": rounds + 1,
        "server": "free (slots released)",
        "pending": count_pending_captures(root),
        "max_attempts": max(attempts) if attempts else 0,
        "entries_still_in_a_BACKOFF_window": blocked_by_backoff,
        "passes": passes,
    })

    # ⛔ "reaches zero" is half the epic's condition; the other half is "and
    # STAYS". A second free pass — a further drain opportunity, one full cycle
    # later — must find nothing to do. Without it a run that filed everything and
    # a run that merely emptied this pass are indistinguishable in the receipt.
    passes = run_round(rounds + 1)
    # MEASURED, not asserted: the receipt's other numbers are readings, so this one
    # must be too — a hardcoded 0 would print "zero" in the very round that exists
    # to prove it, even if a filed entry were left carrying attempts (review finding).
    attempts = [((read_spool_meta(root, f"measured-{i:04d}") or {}).get("attempts", 0))
                for i in range(sessions)]
    rounds_out.append({
        "round": rounds + 2,
        "server": "free (a further cycle — must find nothing to do)",
        "pending": count_pending_captures(root),
        "max_attempts": max(attempts) if attempts else 0,
        "passes": passes,
    })
    return {
        "scenario": "saturated",
        "sessions": sessions,
        "drainers": drainers,
        "rounds": rounds_out,
        "pending_before": pending_before,
        "pending_after": count_pending_captures(root),
        "pending_after_a_further_cycle": rounds_out[-1]["pending"],
        # The falsifier reading: the worst charge observed DURING the run.
        "max_attempts_high_water": high_water,
        # Secondary, and explicitly not the falsifier: filing clears the meta, so
        # this reads 0 in a converged run whatever the saturated rounds charged.
        "max_attempts_after_filing": max(attempts) if attempts else 0,
        "attempts_histogram": {str(n): attempts.count(n) for n in sorted(set(attempts))},
        "attempted": sum(p.get("attempted", 0)
                         for r in rounds_out for p in r["passes"]),
        "filed": server.filed,
        "refusals": server.refusals,
        "peak_slots_held_by_the_drain": server.high_water,
        "claim_race": _claim_race(drainers),
    }


if __name__ == "__main__":
    raise SystemExit(main())
