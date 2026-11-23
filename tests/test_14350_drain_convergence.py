"""D1 (#14342 / #14350) — the drain converges: ONE serialized drainer, an
explicit ceiling, and a capacity refusal that is charged to nobody.

The measured shape this closes (#14342, handoff §7.2–§7.3): 908 spooled sessions,
retry ``attempts`` clustering **10–17**, a live refusal
``{"code":"capture_capacity_saturated","in_flight":8,"limit":8,"retry_after":30}``,
and a backlog that GREW 724 → 908 in one session. Two mechanisms produced it, and
both are in this module's subject:

1. **N drainers.** Every ``SessionStart`` backgrounds ``tortoise session drain``,
   ``SessionEnd`` takes a final flush, ``agent_end`` another, ``store_sync``'s
   sweep is a leg, and both legs (Python + Pi) share one spool directory — so N
   passes POSTed into ONE server ceiling of 8 captures.
2. **A capacity refusal charged to the ENTRY.** ``_flush_one`` incremented
   ``attempts`` and armed an exponential backoff (30 s → 6 h) for a refusal that
   says nothing about the entry — one saturated pass charged EVERY pending entry,
   pushed most of the spool out of the attempt window, and left the next pass
   starting on a different, younger subset. That is the oscillation.

The falsifier (issue body, verbatim): *"if, starting from a non-zero spool, the
head does not reach zero and stay there for a full cycle with no entry exceeding
``attempts = 3``, the patch is not the fix."* ``test_the_falsifier_holds`` runs
exactly that, against a fake server that enforces the real 8-slot ceiling, with
TWO drainer processes racing.

Architecture constraints obeyed (verified, per #14342's research mandate):
``docs/architecture/STORAGE-ARCHITECTURE.md`` §2.0 (Postgres is the record,
FalkorDB a derived cache) is untouched — this is the local spool, upstream of
both, and no raw turn reaches the graph. ``docs/ONTOLOGY.md``'s
episodic-then-semantic order is untouched. ``docs/architecture/
EXTRACTOR-V4-ARCHITECTURE.md`` is untouched: no extraction logic enters the
drain. Nothing here changes the spool's on-disk format (D2 owns that contract).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from tortoise import drain_claim
from tortoise.capture_spool import (
    CAPTURE_CAPACITY_CODE,
    DRAIN_CAPACITY_YIELDS,
    PostOutcome,
    Snapshot,
    _age_key,  # noqa: F401  — the ordering the monotonicity argument rests on
    capacity_refusal,
    capture_health_line,
    count_pending_captures,
    flush_spool,
    read_capture_status,
    read_spool_meta,
    write_spool_entry,
)

ROOT_TURNS = [{"role": "user", "content": "One turn."}]

CAPACITY_BODY = json.dumps({
    "detail": {
        "code": CAPTURE_CAPACITY_CODE,
        "message": "capture capacity saturated",
        "retry_after": 30,
        "in_flight": 8,
        "limit": 8,
    }
})


def _spool(tmp_path: Path, sessions: int = 3, prefix: str = "sess") -> Path:
    root = tmp_path / "spool"
    for i in range(sessions):
        write_spool_entry(
            root,
            Snapshot(session_id=f"{prefix}-{i}", turns=ROOT_TURNS, source="test",
                     machine_id="machine-1"),
        )
    return root


def _capacity(refusals: int = 10 ** 6) -> tuple:
    """A POST that refuses `refusals` times for CAPACITY, then files."""
    state = {"calls": 0, "left": refusals}

    def post(payload: dict) -> PostOutcome:
        state["calls"] += 1
        if state["left"] > 0:
            state["left"] -= 1
            return PostOutcome(ok=False, status=429, detail=CAPACITY_BODY)
        return PostOutcome(ok=True, status=200,
                           body={"session_id": payload["session_id"], "extracted": 1})

    return post, state


# ── The claim: one drainer per spool ──────────────────────────────────────


def test_a_second_drainer_cannot_take_the_claim(tmp_path):
    """MUTATION THAT REDS THIS: drop the `link`/EEXIST arm (or use `rename`)."""
    root = tmp_path / "spool"
    first = drain_claim.acquire(root, leg="python")
    assert first is not None
    assert drain_claim.acquire(root, leg="pi") is None
    record = drain_claim.read_claim(root)
    assert record["leg"] == "python"
    assert record["pid"] == os.getpid()
    assert record["version"] == drain_claim.CLAIM_VERSION
    first.release()
    assert drain_claim.read_claim(root) is None


def test_the_claim_is_written_COMPLETE_never_visible_empty(tmp_path):
    """A reader that sees a zero-length claim reads it as corrupt (rule 1) and
    would STEAL a live claim mid-write.

    MUTATION THAT REDS THIS: replace the tmp+`link` pair with
    `open(path, O_CREAT|O_EXCL)` followed by a write — the claim is then visible
    before its bytes are.
    """
    root = tmp_path / "spool"
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None
    raw = drain_claim.claim_path(root).read_text(encoding="utf-8")
    assert json.loads(raw)["token"] == claim.token
    # …and no scratch file is left behind for the spool's accounting to find.
    assert [p.name for p in root.iterdir() if p.name.startswith("drain.claim.tmp-")] == []
    claim.release()


def test_an_EXPIRED_claim_is_taken_over_not_a_permanent_block(tmp_path):
    """The issue's own requirement: "a stale claim is re-verifiable, not a
    permanent block". A killed drainer's claim must not wedge the box.

    MUTATION THAT REDS THIS: return None for any existing claim file.
    """
    root = tmp_path / "spool"
    dead = drain_claim.acquire(root, leg="python", now=1000.0, ttl_s=60.0)
    assert dead is not None
    assert drain_claim.acquire(root, leg="pi", now=1030.0, ttl_s=60.0) is None, (
        "inside the lease a live claim must be honoured")
    taken = drain_claim.acquire(root, leg="pi", now=1100.0, ttl_s=60.0)
    assert taken is not None, "an expired claim is not a permanent block"
    assert drain_claim.read_claim(root)["leg"] == "pi"
    taken.release()


def test_a_DEAD_HOLDERS_claim_is_reclaimed_without_waiting_for_expiry(tmp_path):
    """Lease expiry is the OUTER bound; a provably dead pid is the fast one, so a
    box whose drainer was SIGKILLed recovers on the next opportunity.

    MUTATION THAT REDS THIS: skip the pid arm of `claim_is_stale` (or read
    `os.kill`'s EPERM as dead — that would let one user steal another's claim).
    """
    root = tmp_path / "spool"
    claim = drain_claim.acquire(root, leg="python", ttl_s=600.0)
    assert claim is not None
    record = drain_claim.read_claim(root)
    record["pid"] = 999_999_999  # no such process
    drain_claim.claim_path(root).write_text(json.dumps(record), encoding="utf-8")
    assert drain_claim.claim_is_stale(
        record, now_s=time.time(), host=record["host"],
        boot_id=record.get("boot_id")) is True
    taken = drain_claim.acquire(root, leg="pi", ttl_s=600.0)
    assert taken is not None
    assert drain_claim.read_claim(root)["leg"] == "pi"
    taken.release()


def test_a_LIVE_holders_claim_is_honoured_and_never_deleted(tmp_path):
    """The safe direction: honouring a dead claim costs one skipped pass; stealing
    a live one is TWO DRAINERS, the defect this module exists to remove.

    MUTATION THAT REDS THIS: read `pid_alive`'s unprovable `OSError` as dead.
    """
    root = tmp_path / "spool"
    claim = drain_claim.acquire(root, leg="python", ttl_s=600.0)
    assert claim is not None
    record = drain_claim.read_claim(root)
    assert drain_claim.claim_is_stale(
        record, now_s=time.time(), host=record["host"],
        boot_id=record.get("boot_id")) is False
    assert drain_claim.acquire(root, leg="pi", ttl_s=600.0) is None
    assert drain_claim.read_claim(root)["token"] == claim.token
    claim.release()


def _ms(seconds: float) -> float:
    """Seconds -> the claim's ON-DISK unit (milliseconds; see TIMESTAMP_UNIT_MS).

    Every hand-built record in this module goes through here, so a test can never
    quietly disagree with the unit the code writes — the mismatch that let each leg
    steal the other's live claim.
    """
    return seconds * drain_claim.TIMESTAMP_UNIT_MS


def test_a_reboot_makes_a_same_hosts_claim_stale(tmp_path):
    """A rebooted box can REUSE a pid, and a reused pid is not the holder.

    MUTATION THAT REDS THIS: drop the boot_id comparison (the claim then survives
    a reboot on a pid that now belongs to an unrelated process, and no drain ever
    runs again).
    """
    record = {
        "version": drain_claim.CLAIM_VERSION, "token": "t", "leg": "pi",
        "pid": os.getpid(), "host": "box-a", "boot_id": "boot-1",
        "started_at": 0.0, "expires_at": _ms(time.time() + 600.0),
    }
    assert drain_claim.claim_is_stale(
        record, now_s=time.time(), host="box-a", boot_id="boot-1") is False
    assert drain_claim.claim_is_stale(
        record, now_s=time.time(), host="box-a", boot_id="boot-2") is True


def test_a_FOREIGN_hosts_claim_is_judged_by_the_lease_only(tmp_path):
    """A copied spool can carry another host's claim; this box cannot check that
    host's pids, so only expiry may end it.

    MUTATION THAT REDS THIS: run the pid arm for a foreign host (a *local* pid
    that happens to be dead then steals a claim the real holder still holds).
    """
    record = {
        "version": drain_claim.CLAIM_VERSION, "token": "t", "leg": "python",
        "pid": 999_999_999, "host": "another-box", "boot_id": "boot-9",
        "started_at": 0.0, "expires_at": _ms(2000.0),
    }
    assert drain_claim.claim_is_stale(
        record, now_s=1000.0, host="box-a", boot_id="boot-1") is False
    assert drain_claim.claim_is_stale(
        record, now_s=2000.0, host="box-a", boot_id="boot-1") is True


@pytest.mark.parametrize("junk", [
    None, [], "text", {}, {"expires_at": "soon"}, {"expires_at": True},
    {"expires_at": float("nan")}, {"expires_at": float("inf")},
])
def test_an_UNATTRIBUTABLE_claim_is_stale(junk):
    """Rule 1, and it is TOTAL: nothing that fails to say WHEN it ends may hold
    the spool forever.

    MUTATION THAT REDS THIS: compare `record["expires_at"] > now` directly (a
    string raises, and a NaN compares False → the drain never runs again, silently
    and forever).
    """
    assert drain_claim.claim_is_stale(junk, now_s=1000.0) is True


def test_a_SUCCESSORS_claim_survives_a_slow_holders_release(tmp_path):
    """Ownership is the TOKEN, and release is compare-and-delete: a holder whose
    lease expired and was taken over must not delete the claim that replaced it.

    MUTATION THAT REDS THIS: unlink unconditionally in `release`.
    """
    root = tmp_path / "spool"
    slow = drain_claim.acquire(root, leg="python", now=1000.0, ttl_s=60.0)
    assert slow is not None
    successor = drain_claim.acquire(root, leg="pi", now=1100.0, ttl_s=60.0)
    assert successor is not None
    assert slow.release() is False, "the token is no longer ours"
    assert slow.renew(now_s=1105.0) is False
    still = drain_claim.read_claim(root)
    assert still is not None and still["token"] == successor.token, (
        "a late release from the previous holder must not remove the successor")
    successor.release()


def test_a_release_does_not_MOVE_a_claim_that_is_not_ours(tmp_path):
    """A not-ours release must touch nothing at all.

    The takedown-then-compare version `rename`d the successor's claim aside before
    discovering it was not ours; if a THIRD process installed into the now-empty
    path, the restore `link` failed `EEXIST` and the successor's bytes were
    unlinked — a live claim destroyed by a release that was trying to be polite
    (#14423 review, P2; reproduced: `release()` returned False and the successor's
    claim was GONE).

    MUTATION THAT REDS THIS: take the claim aside before reading its token (the
    file's inode then changes, and a third party's install lands in the gap).
    """
    root = tmp_path / "spool"
    slow = drain_claim.acquire(root, leg="python", now=1000.0, ttl_s=60.0)
    assert slow is not None
    successor = drain_claim.acquire(root, leg="pi", now=1100.0, ttl_s=60.0)
    assert successor is not None
    before = os.stat(drain_claim.claim_path(root))
    assert slow.release() is False, "the token is no longer ours"
    after = os.stat(drain_claim.claim_path(root))
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns), (
        "a release that does not own the claim must not move, replace or touch it")
    assert drain_claim.read_claim(root)["token"] == successor.token
    successor.release()


def test_an_ABSURD_but_finite_lease_cannot_block_the_spool(tmp_path):
    """A claim we can read but whose lease is out of range must be REPORTED, not
    raised out of the drain.

    `time.gmtime` raises on an out-of-range instant, and the operator line is built
    OUTSIDE `flush_spool`'s try/finally — so one corrupt-but-parseable claim file
    (a huge `expires_at`, honoured by rule 2 because it never expires) aborted
    every later `session drain` with `OverflowError` and blocked the spool forever
    (#14423 review, P2; reproduced end-to-end).

    MUTATION THAT REDS THIS: format the instant unguarded.
    """
    root = tmp_path / "spool"
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None
    record = drain_claim.read_claim(root)
    record["expires_at"] = 1e30  # seconds, finite, out of range for BOTH clocks
    record["host"] = "another-box"  # so only rule 2 may decide — it never expires
    drain_claim.claim_path(root).write_text(json.dumps(record), encoding="utf-8")
    held = drain_claim.holder(root)
    assert held is not None
    assert drain_claim.claim_is_stale(
        held.record, now_s=time.time(), host="box-a", boot_id="boot-1") is False
    line = held.line(root)   # must not raise
    assert "another drainer" in line and "lease to" not in line, line
def test_the_claim_lives_in_the_spool_ROOT_so_it_is_never_COUNTED(tmp_path):
    """A bookkeeping file inside `entries/` would be spool occupancy forever —
    counted by `_entry_bytes`, ranked by `prune_spool`, and able to push a box
    over a ceiling it can never get back under (the `status.json` rule).

    MUTATION THAT REDS THIS: put the claim under `entries/`.
    """
    root = _spool(tmp_path, sessions=2)
    before = count_pending_captures(root)
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None
    assert drain_claim.claim_path(root) == root / drain_claim.CLAIM_NAME
    assert not drain_claim.claim_path(root).is_relative_to(root / "entries")
    assert count_pending_captures(root) == before
    claim.release()
    assert count_pending_captures(root) == before


# ── flush_spool: serialized, and capacity charges nobody ──────────────────


def test_a_HELD_claim_makes_a_whole_spool_pass_attempt_NOTHING(tmp_path):
    """The second of two concurrent drains does nothing AT ALL — not one POST, not
    one status write (that would erase what the holder last observed).

    MUTATION THAT REDS THIS: run the pass anyway (the pre-#14350 shape).
    """
    root = _spool(tmp_path, sessions=4)
    holder = drain_claim.acquire(root, leg="pi")
    assert holder is not None
    posts: list[str] = []

    def post(payload):
        posts.append(payload["session_id"])
        return PostOutcome(ok=True, status=200, body={})

    summary = flush_spool(root, post, now=1000.0)
    assert summary.held_by_claim is True
    assert posts == []
    assert summary.attempted == 0
    assert summary.claim_holder and "another drainer" in summary.claim_holder
    assert read_capture_status(root) is None, (
        "a pass that observed nothing must not overwrite the holder's record")
    holder.release()


def test_a_TARGETED_pass_is_NOT_blocked_by_the_claim(tmp_path):
    """`session capture` and the shutdown flush file ONE named session and must
    never be starved behind a whole-spool drain — one POST is the most they hold.

    MUTATION THAT REDS THIS: take the claim for every pass (`only_session_id` or
    not) — the live session's own final filing then waits on a backlog.
    """
    root = _spool(tmp_path, sessions=3)
    holder = drain_claim.acquire(root, leg="pi")
    assert holder is not None
    posts: list[str] = []

    def post(payload):
        posts.append(payload["session_id"])
        return PostOutcome(ok=True, status=200, body={})

    summary = flush_spool(root, post, now=1000.0, only_session_id="sess-1")
    assert summary.held_by_claim is False
    assert posts == ["sess-1"]
    holder.release()


def test_the_claim_is_released_even_when_the_pass_RAISES(tmp_path):
    """A pass that throws must not leave the spool locked for the whole lease —
    that is the difference between a lock and a wedge.

    MUTATION THAT REDS THIS: release after the loop instead of in a `finally`.
    """
    root = _spool(tmp_path, sessions=2)

    def post(payload):
        raise KeyboardInterrupt("killed mid-pass")

    with pytest.raises(KeyboardInterrupt):
        flush_spool(root, post, now=1000.0)
    assert drain_claim.read_claim(root) is None, "the claim must be released"
    assert drain_claim.acquire(root, leg="pi") is not None, (
        "and the next drainer must be able to take it immediately")


def test_the_claim_is_released_after_a_normal_pass(tmp_path):
    root = _spool(tmp_path, sessions=2)
    flush_spool(root, lambda payload: PostOutcome(ok=True, status=200, body={}),
                now=1000.0)
    assert drain_claim.read_claim(root) is None


def test_a_CAPACITY_refusal_charges_NOTHING_and_stops_the_pass(tmp_path):
    """#14342 §7.3 in one test. The refusal is about the BOX, so no entry may be
    charged and no backoff armed — that is what made `attempts` reach 10–17.

    Asserted together, because any one alone is satisfiable the wrong way:
      * attempts stay 0 on EVERY entry (nothing charged);
      * `next_attempt_at_ms` stays 0 (no window armed);
      * ONE entry is attempted per refusal, not all N (the pass stops);
      * the yields are bounded, and honour the server's own `retry_after`.

    MUTATION THAT REDS THIS: delete the `capacity_refusal` branch in `_flush_one`
    (the refusal then lands in the per-entry backoff write-back).
    """
    root = _spool(tmp_path, sessions=5)
    post, state = _capacity()
    waits: list[float] = []
    summary = flush_spool(root, post, now=1000.0, sleep=waits.append)

    assert summary.capacity_stopped is True
    assert summary.capacity_yields == DRAIN_CAPACITY_YIELDS
    assert waits == [30.0] * DRAIN_CAPACITY_YIELDS, (
        "the drain honours the server's own retry_after — it does not invent one")
    assert state["calls"] == 1 + DRAIN_CAPACITY_YIELDS, (
        "one POST per yield, and the SAME entry retried — never a walk over the "
        "whole backlog")
    for i in range(5):
        meta = read_spool_meta(root, f"sess-{i}")
        assert meta["attempts"] == 0, f"sess-{i} was charged for the box's ceiling"
        assert meta["next_attempt_at_ms"] == 0


def test_a_capacity_refusal_is_NAMED_durably_and_clears_on_the_next_pass(tmp_path):
    """The hook that fires the drain throws its stderr away, so the console line
    is not enough: the cause has to be on disk, in the shape the health line
    already renders, and it has to clear itself.

    MUTATION THAT REDS THIS: leave the capacity refusal to the `attempted > 0`
    arm (it records `ok` for a box that cannot file — the false PASS).
    """
    root = _spool(tmp_path, sessions=3)
    post, _ = _capacity()
    flush_spool(root, post, now=1000.0, sleep=lambda _s: None)

    status = read_capture_status(root)
    assert status["state"] == "blocked"
    assert status["status"] == 429
    assert status["reason"] == CAPTURE_CAPACITY_CODE
    line = capture_health_line(root, api_key="k").line
    assert line.startswith("capture: BLOCKED (3 queued, 429 capture capacity)"), line

    # …and a pass with a free slot clears it (self-healing, both ends).
    flush_spool(root, lambda p: PostOutcome(ok=True, status=200, body={}), now=2000.0)
    assert read_capture_status(root)["state"] == "ok"
    assert capture_health_line(root, api_key="k").line == "capture: OK (0 queued)"


def test_a_BARE_429_is_also_a_box_level_refusal(tmp_path):
    """THE STATUS IS THE TRIGGER, THE CODE IS THE NAME. A 429 with no typed body
    (an older server, an upstream limiter, a proxy) is still a saturation signal,
    and charging the entry for it would leave the measured defect alive on every
    deployment that does not send the category — the entry would carry a backoff
    that says "this payload was refused", which nothing observed.

    MUTATION THAT REDS THIS: require the typed code (`if status != 429 and not
    typed` → `if not typed`) — this entry is then charged and the pass walks on.
    """
    root = _spool(tmp_path, sessions=2)

    def post(payload):
        return PostOutcome(ok=False, status=429, detail="Too Many Requests")

    summary = flush_spool(root, post, now=1000.0, sleep=lambda _s: None)
    assert summary.capacity_stopped is True
    assert summary.capacity == {"status": 429, "code": CAPTURE_CAPACITY_CODE,
                                "detail": "Too Many Requests"}
    assert summary.capacity_yields == DRAIN_CAPACITY_YIELDS
    for i in range(2):
        meta = read_spool_meta(root, f"sess-{i}")
        assert meta["attempts"] == 0 and meta["next_attempt_at_ms"] == 0


def test_a_capacity_stop_does_not_skip_the_OLDEST_entry(tmp_path):
    """A busy box must not starve the oldest capture: after yielding, the pass
    retries THE SAME entry and files it, still charging nothing.

    MUTATION THAT REDS THIS: `index += 1` on a capacity stop (the refusal is then
    attributed to the entry and the oldest one drifts back in the queue).
    """
    root = _spool(tmp_path, sessions=3)
    post, _state = _capacity(refusals=2)
    submitted: list[str] = []
    inner = post

    def recording(payload):
        submitted.append(payload["session_id"])
        return inner(payload)

    summary = flush_spool(root, recording, now=1000.0, sleep=lambda _s: None)
    assert summary.filed == 3
    assert summary.capacity_yields == 2
    assert submitted == ["sess-0", "sess-0", "sess-0", "sess-1", "sess-2"], (
        "the yields retry the SAME entry; then the walk continues")
    assert read_spool_meta(root, "sess-0")["attempts"] == 0
    assert read_spool_meta(root, "sess-0")["filed_key"]


def test_the_ceiling_is_ONE_post_in_flight_and_it_is_stated():
    """The epics's "explicit and stated ceiling". ONE, and it is the drain's share
    of the box's shared capture budget — not a throughput knob.

    MUTATION THAT REDS THIS: raise it (nothing else in the code reads it, which is
    the point: the value IS the statement).
    """
    from tortoise.capture_spool import DRAIN_MAX_IN_FLIGHT
    assert DRAIN_MAX_IN_FLIGHT == 1


def test_an_UNWRITABLE_spool_root_runs_UNCLAIMED_and_says_so(tmp_path):
    """No claim can be created ⇒ the pass still runs and reports the broken spool.
    Refusing to run would hide the `UNREADABLE` verdict — the false-PASS
    direction, which this codebase treats as the worse error.

    MUTATION THAT REDS THIS: return the held-by-claim summary from the
    `DrainClaimUnavailable` arm (a mode-000 root then reads as "another drainer
    has it", and no surface ever says the spool is unreadable).
    """
    if os.getuid() == 0:
        pytest.skip("root ignores mode bits")
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    os.chmod(root, 0o000)
    try:
        summary = flush_spool(root, lambda p: PostOutcome(ok=True, status=200, body={}),
                              now=1000.0)
    finally:
        os.chmod(root, 0o700)
    assert summary.held_by_claim is False
    assert summary.claim_unavailable is True


# ── The falsifier ─────────────────────────────────────────────────────────


class _CeilingServer:
    """The server's capture gate, as `hosted_api._reserve_capture_slot` states it.

    8 concurrent captures per box, then 429 + `retry_after`. It also records the
    HIGH-WATER MARK of in-flight captures, which is the drain's own footprint: the
    ceiling claim ("one drainer holds ONE slot of eight") is measured here rather
    than asserted.
    """

    def __init__(self, limit: int = 8, post_seconds: float = 0.001) -> None:
        self.limit = limit
        #: ``True`` makes the ceiling unreachable — the shape a box is in while
        #: the user's OWN captures hold every slot (§7.3's live measurement).
        self.externally_saturated = False
        self.post_seconds = post_seconds
        self.in_flight = 0
        self.high_water = 0
        self.refusals = 0
        self.filed = 0
        self._lock = threading.Lock()

    def post(self, payload: dict) -> PostOutcome:
        with self._lock:
            self.in_flight += 1
            self.high_water = max(self.high_water, self.in_flight)
            saturated = self.externally_saturated or self.in_flight > self.limit
            if saturated:
                self.refusals += 1
        try:
            if saturated:
                return PostOutcome(ok=False, status=429, detail=CAPACITY_BODY)
            time.sleep(self.post_seconds)
            with self._lock:
                self.filed += 1
            return PostOutcome(ok=True, status=200,
                               body={"session_id": payload["session_id"],
                                     "extracted": 1})
        finally:
            with self._lock:
                self.in_flight -= 1


def test_the_falsifier_holds(tmp_path):
    """**Starting from a non-zero spool, the head reaches ZERO and STAYS there for
    a full cycle with no entry exceeding `attempts = 3`** — with TWO drainers
    racing, against a server that enforces the real 8-slot ceiling.

    ⛔ WHAT THIS TEST DOES AND DOES NOT EXERCISE (corrected in review: it claimed
    more than it measured). It runs two drainers as THREADS, because the claim is
    a file and excludes threads exactly as it excludes processes — process
    exclusion is measured separately and honestly, by
    `tools/measure_drain_convergence.py`'s 10-process race (1 winner).
    And with TWO drainers it never saturates the 8-slot ceiling, so NO capacity
    refusal occurs here at all: `server.refusals == 0` is asserted below rather
    than implied. The capacity half of the shape gets its own forcing test
    (`test_a_CAPACITY_refusal_charges_NOBODY`), which drives the refusal
    directly. This test therefore proves the SERIALIZATION + convergence half
    only, and says so.

    The `high_water` assertion is the ceiling's own measurement: a serialized
    drain holds ONE of the eight slots, so a live capture can never be starved.

    MUTATION THAT REDS THIS: drop the claim — both passes run concurrently, the
    ceiling's counter climbs, and (at 8 in flight) refusals appear that charge the
    entries, pushing attempts past 3.
    """
    root = _spool(tmp_path, sessions=40)
    assert count_pending_captures(root) == 40
    server = _CeilingServer()
    summaries = []

    def drainer() -> None:
        summaries.append(flush_spool(root, server.post, now=time.time() * 1000.0,
                                     sleep=lambda _s: None))

    threads = [threading.Thread(target=drainer) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert count_pending_captures(root) == 0, "the head must reach ZERO"
    assert sum(1 for s in summaries if s.held_by_claim) == 1, (
        "exactly ONE drainer runs; the other is a no-op, not a competitor")
    for i in range(40):
        meta = read_spool_meta(root, f"sess-{i}")
        assert meta.get("attempts", 0) <= 3, f"sess-{i} exceeded attempts = 3"
    assert server.high_water <= 1, (
        f"the serialized drain held {server.high_water} capture slots — the whole "
        "point of ONE in-flight POST is that the other seven stay free")
    assert server.refusals == 0, (
        "nothing here should have reached the ceiling; if refusals appear, read the "
        "attempts assertion above as the capacity-charging bug, not this one")

    # …and it STAYS there for a full cycle: another pass files nothing, defers
    # nothing, and charges nobody.
    settled = flush_spool(root, server.post, now=time.time() * 1000.0)
    assert settled.filed == 0 and settled.deferred == 0
    assert count_pending_captures(root) == 0
    assert drain_claim.read_claim(root) is None

    # ── The CAPACITY half, FORCED (the phase above never reaches the ceiling) ──
    #
    # The same spool, with the user's own captures holding every slot: the drain
    # is refused for the BOX's capacity on every POST. This is exactly §7.3's live
    # measurement, and the assertion is that it costs NOTHING — no `attempts`, no
    # backoff window — so the moment the slots free, the SAME pass files
    # everything. Before #14350 each of these refusals charged the entry and armed
    # an exponential backoff, which is how 908 entries reached `attempts` 10–17.
    root2 = _spool(tmp_path / "saturated", sessions=40)
    server2 = _CeilingServer()
    server2.externally_saturated = True
    for _ in range(4):  # four saturated opportunities = a full backoff cliff
        blocked = flush_spool(root2, server2.post, now=time.time() * 1000.0,
                              sleep=lambda _s: None)
        assert blocked.capacity_stopped is True
        assert blocked.filed == 0
    assert server2.refusals > 0, "the phase must actually meet the ceiling"
    for i in range(40):
        meta = read_spool_meta(root2, f"sess-{i}")
        assert meta.get("attempts", 0) == 0, (
            f"sess-{i} was charged {meta.get('attempts')} attempts for the BOX's "
            "refusal — this is the self-feeding backlog")
        assert not meta.get("next_attempt_at_ms"), (
            "a capacity refusal must not arm a backoff window")
    assert count_pending_captures(root2) == 40, "nothing filed, nothing lost"

    server2.externally_saturated = False
    freed = flush_spool(root2, server2.post, now=time.time() * 1000.0,
                        sleep=lambda _s: None)
    assert freed.filed == 40, "the freed pass files ALL of it — no entry left behind"
    assert count_pending_captures(root2) == 0


# ── Findings from the first review cycle ──────────────────────────────────


def test_an_EXPIRED_lease_is_never_RESURRECTED_by_its_own_holder(tmp_path):
    """A holder that overran its lease must not renew it back to life.

    `renew` reads the record and then `os.replace`s its own — a read-then-replace
    with no compare. So when OUR claim has EXPIRED but a peer has not yet taken it
    over, every check `renew` can make still passes (the token on disk IS ours),
    and without an expiry rule it happily writes a fresh lease and returns `True`.
    The peer that read the expired claim in that window and took it over is then
    silently overwritten — two drainers, one of them certain it is alone.

    ⛔ The assertion must be on an EXPIRED-BUT-STILL-OURS claim. Renewing after a
    successor has already taken over returns False through the TOKEN check, which
    is a different guard, and a test written that way passes with the expiry rule
    deleted (measured: it did).

    MUTATION THAT REDS THIS: delete the `now >= expires` guard from `renew`.
    """
    root = tmp_path / "spool"
    claim = drain_claim.acquire(root, leg="python", now=1000.0, ttl_s=60.0)
    assert claim is not None
    # Inside the lease: renewable.
    assert claim.renew(now_s=1030.0) is True
    # Past it, and STILL the token owner on disk — the window the guard exists for.
    on_disk = drain_claim.read_claim(root)
    assert on_disk is not None and on_disk["token"] == claim.token
    assert claim.renew(now_s=1200.0) is False, (
        "an expired lease is not renewable — that is how a peer's takeover gets "
        "silently overwritten")
    assert drain_claim.read_claim(root)["expires_at"] <= _ms(1200.0), (
        "the record must not have been pushed into the future")
    # And a peer may then take it, as it always could.
    successor = drain_claim.acquire(root, leg="pi", now=1201.0, ttl_s=60.0)
    assert successor is not None
    successor.release()


def test_a_TAKEDOWN_never_deletes_a_live_claim_installed_under_it(tmp_path):
    """The read-compare-unlink takedown was racy, and the race restored the exact
    defect this module exists to remove: two processes judging one stale claim
    both pass the compare, one installs its own claim, and the other's `unlink`
    removes the LIVE one — two holders until the loser's next refresh.

    This models that interleaving directly: a claim is judged stale and moved
    aside, a NEW claim is installed, and the stale takedown must then decline and
    restore rather than dispose.

    MUTATION THAT REDS THIS: go back to compare-then-unlink (the taken claim's
    bytes are no longer what was judged stale, so the successor is deleted).
    """
    root = tmp_path / "spool"
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None
    stale_bytes = drain_claim.claim_path(root).read_bytes()
    successor = drain_claim.acquire(root, leg="pi")  # a live claim replaces it
    assert successor is None, "the first claim is still live, so it must be honoured"
    # Simulate the interleaving at the primitive level.
    moved = drain_claim._takedown(drain_claim.claim_path(root), "contender")
    assert moved is not None
    quarantine, data = moved
    # A THIRD process installs its claim while the stale taker was moving files.
    _ = stale_bytes
    fresh = drain_claim.acquire(root, leg="python")
    assert fresh is not None
    restored_ok = drain_claim._dispose_quarantine(
        quarantine, data, drain_claim.claim_path(root), b"<not what was judged stale>")
    assert restored_ok is False, "a claim that is not the one judged stale is kept"
    on_disk = drain_claim.read_claim(root)
    assert on_disk is not None and on_disk["token"] == fresh.token, (
        "the live claim must survive a stale taker's disposal attempt")
    fresh.release()


def test_a_FUTURE_version_claim_is_unattributable_not_reinterpreted(tmp_path):
    """The version field is load-bearing: a record this build does not understand
    must be staled out (then re-verifiable by expiry), never read with today's
    semantics.

    MUTATION THAT REDS THIS: drop the version rule from `claim_is_stale`.
    """
    record = {
        "version": drain_claim.CLAIM_VERSION + 1, "token": "t", "leg": "pi",
        "pid": os.getpid(), "host": "box-a", "boot_id": "boot-1",
        "started_at": 0.0, "expires_at": _ms(time.time() + 600.0),
    }
    assert drain_claim.claim_is_stale(record, now_s=time.time()) is True


def test_the_spool_root_is_created_0700(tmp_path):
    """A drain can be the FIRST thing to touch a fresh spool (SessionStart fires it
    before any capture). The directory then holds conversation-derived entry names,
    so it must be private — every other creator in `capture_spool` passes 0700.

    MUTATION THAT REDS THIS: drop `mode=0o700` from the mkdir.
    """
    if os.getuid() == 0:
        pytest.skip("root ignores mode bits")
    root = tmp_path / "fresh" / "spool"
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None
    assert (root.stat().st_mode & 0o777) == 0o700
    claim.release()


def test_a_TARGETED_pass_does_NOT_yield_on_a_capacity_refusal(tmp_path):
    """`session capture` and the SessionEnd flush run inside a HOOK BUDGET
    (`session-end.sh` sets `"timeout": 60`), so a targeted pass must not sleep:
    one POST, one refusal, return. Only the DETACHED drain may politeness-wait.

    MUTATION THAT REDS THIS: gate the yield on `summary.capacity_stopped` alone
    (drop `claim is not None`) — the targeted pass then sleeps 90 s and the hook
    is cancelled mid-pass.
    """
    root = _spool(tmp_path, sessions=2)
    post, state = _capacity()
    waits: list[float] = []
    summary = flush_spool(root, post, now=1000.0, sleep=waits.append,
                          only_session_id="sess-0")
    assert waits == [], "a targeted pass never yields"
    assert state["calls"] == 1
    assert summary.capacity_yields == 0
    assert summary.capacity_stopped is True
    assert read_spool_meta(root, "sess-0")["attempts"] == 0


def test_a_crashed_takers_quarantine_is_COLLECTED_by_the_scratch_sweeper(
        tmp_path, monkeypatch):
    """A taker killed between the claim's rename and its disposal must not leave a
    file nothing ever collects.

    The spool ROOT holds only `drain.claim` and `status.json`, so no spool scan
    walks it — the only collector is `capture_spool`'s mtime-based `*.tmp-*`
    sweep. The quarantine therefore has to MATCH that glob, which is why its name
    carries a `.tmp-` infix rather than just `.take-`.

    ⛔ The leftover is produced by an INTERRUPTED TAKEDOWN, not by writing a
    hand-named file: a test that spells the name itself asserts the sweeper's glob,
    not the implementation's choice of name, and passes with the infix removed
    (measured — it did).

    MUTATION THAT REDS THIS: name the quarantine `{path.name}.take-{pid}-{token}`.
    """
    from tortoise.capture_spool import _sweep_stale_temp_files

    root = tmp_path / "spool"
    stale = drain_claim.acquire(root, leg="python", now=1000.0, ttl_s=10.0)
    assert stale is not None  # left in place, and EXPIRED at the taker's clock

    # The taker MOVES the stale claim aside and then dies before disposing of it.
    monkeypatch.setattr(drain_claim, "_dispose_quarantine",
                        lambda *a, **k: False)
    taker = drain_claim.acquire(root, leg="pi", now=2000.0, ttl_s=10.0)
    assert taker is None, "the interrupted taker gives up rather than proceeding"
    leftovers = [p for p in root.iterdir() if p.name != "drain.claim"]
    assert leftovers, "the interrupted takedown left its quarantine behind"
    ages = [(p, time.time() - 7200) for p in leftovers]
    for path, old in ages:
        os.utime(path, (old, old))
    _sweep_stale_temp_files(root)
    assert not [p for p in root.iterdir() if p.name != "drain.claim"], (
        "an abandoned quarantine must be swept like any other scratch file; "
        "otherwise the spool root grows a file per crash, forever: "
        f"{sorted(p.name for p in root.iterdir())}")


# ── THE CROSS-LEG CONTRACT ─────────────────────────────────────────────────
#
# ⛔ The two legs write ONE file and each has its own predicate, so the protocol
# is only as good as their AGREEMENT — and nothing tested that agreement. A
# reviewer found the consequence: Python wrote `expires_at` in SECONDS while the
# Pi leg wrote MILLISECONDS, so each leg read the other's live claim as long
# expired and STOLE it (two drainers), and a Pi claim read by Python was live for
# ~55,000 years (a permanent block). Both legs' suites were green, because each
# only ever built records in its OWN unit.

_NODE = shutil.which("node")
_TS_LEG = Path(__file__).resolve().parent.parent / "tortoise/pi-hooks/tortoise-capture.ts"


def _ts_verdict(root: Path) -> dict:
    """Ask the REAL TypeScript leg what it makes of the claim at ``root``.

    Runs the shipped predicate, not a re-implementation: a Python copy of the TS
    rules would agree with the Python rules by construction and prove nothing.
    """
    script = (
        f'import {{ acquireDrainClaim, claimIsStale, readDrainClaim }} from {json_dumps(_TS_LEG.as_posix())};\n'
        'const dir = process.env.CLAIM_DIR;\n'
        'const rec = readDrainClaim(dir);\n'
        'const stolen = acquireDrainClaim(dir, { leg: "pi" });\n'
        'process.stdout.write(JSON.stringify({\n'
        '  readable: !!rec,\n'
        '  stale: claimIsStale(rec, { nowMs: Date.now() }),\n'
        '  stole: !!stolen,\n'
        '  seconds_scale: rec ? rec.expires_at < 1e11 : null,\n'
        '  version: rec ? rec.version : null,\n'
        '}));\n'
    )
    # `node -e` shifts argv (the script is argv[0]), so the path goes through the
    # environment: an off-by-one here would make the test pass against the WRONG
    # directory, which is the class of failure this test exists to catch.
    out = subprocess.run([_NODE, "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=120,
                         env={**os.environ, "CLAIM_DIR": str(root)})
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


def json_dumps(value: str) -> str:
    return json.dumps(value)


@pytest.mark.skipif(_NODE is None, reason="node is not installed on this box")
def test_the_PI_LEG_honours_a_LIVE_python_claim(tmp_path):
    """A live Python claim must read as LIVE to the Pi leg, and must not be stolen.

    THIS IS THE TEST WHOSE ABSENCE LET THE P0 SHIP: the unit of `expires_at` was
    seconds here and milliseconds there, every other test built records in its own
    leg's unit, and each leg therefore stole the other's live claim.

    MUTATION THAT REDS THIS: write seconds again (`now_s + ttl_s`), or drop
    TIMESTAMP_UNIT_MS from `claim_is_stale`.
    """
    root = tmp_path / "spool"
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None
    before = drain_claim.read_claim(root)
    # TWICE: a mutation that advances the acquisition time only takes effect on the
    # NEXT write, so one renewal cannot see it.
    assert claim.renew(now_s=time.time() + 10) is True
    assert claim.renew(now_s=time.time() + 30) is True
    after = drain_claim.read_claim(root)
    assert after["started_at"] == before["started_at"], (
        "`started_at` is when the claim was ACQUIRED — a renewal must not move it "
        "(the Pi leg preserves it; a disagreement here is a field-level divergence "
        "in a file whose contract is field-level agreement)")
    verdict = _ts_verdict(root)
    assert verdict["readable"] is True, "the Pi leg must be able to read our record"
    assert verdict["version"] == drain_claim.CLAIM_VERSION
    assert verdict["stale"] is False, (
        "a LIVE python claim read as STALE by the Pi leg — the unit mismatch is "
        "back (seconds vs milliseconds), and the Pi leg will steal it")
    assert verdict["stole"] is False, (
        "the Pi leg STOLE a live python claim — two drainers, the defect D1 removes")
    claim.release()


@pytest.mark.skipif(_NODE is None, reason="node is not installed on this box")
def test_a_PI_claim_is_read_in_the_SAME_UNIT_by_both_legs(tmp_path):
    """The reverse direction: a record the Pi leg writes must be judged by the same
    clock here.

    A millisecond record read by a seconds-based predicate is live for ~55,000
    years — the "a stale claim is never a permanent block" rule, inverted.

    MUTATION THAT REDS THIS: compare `now_s >= expires` (drop the conversion).
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True, exist_ok=True)
    script = (
        f'import {{ acquireDrainClaim }} from {json_dumps(_TS_LEG.as_posix())};\n'
        'const c = acquireDrainClaim(process.env.CLAIM_DIR, { leg: "pi", ttlMs: 60000 });\n'
        'process.stdout.write(JSON.stringify({ ok: !!c }));\n'
    )
    out = subprocess.run([_NODE, "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=120,
                         env={**os.environ, "CLAIM_DIR": str(root)})
    assert out.returncode == 0, out.stderr[-2000:]
    record = drain_claim.read_claim(root)
    assert record is not None, "the Pi leg's claim must be readable here"
    # Its lease is 60 s: LIVE now, and EXPIRED one minute later. A unit mismatch
    # shows up as one of those two failing.
    now_s = time.time()
    assert drain_claim.claim_is_stale(record, now_s=now_s) is False, (
        "a live Pi claim read as stale here")
    assert drain_claim.claim_is_stale(record, now_s=now_s + 120) is True, (
        "a Pi claim never expires here — that is the permanent-block direction of "
        "the unit mismatch")
    # The magnitude IS the contract: the lease is ~60 s, so on the millisecond
    # clock this is ~now_ms, and on a seconds clock it would be ~now_ms/1000. A
    # ratio rather than a magic constant, so the test cannot drift with the clock.
    ratio = record["expires_at"] / (time.time() * drain_claim.TIMESTAMP_UNIT_MS)
    assert 0.999 < ratio < 1.01, (
        f"expires_at is on a different clock than the Pi leg writes (ratio {ratio:.4f}) "
        "— the on-disk unit is MILLISECONDS (TIMESTAMP_UNIT_MS), and a mismatch lets "
        "each leg steal the other's live claim")


# ── An UNREADABLE claim must not become a permanent block ─────────────────


def test_a_DIRECTORY_at_the_claim_path_is_cleared_not_honoured(tmp_path):
    """Corruption at the claim path must be CLEARED, not read as a holder.

    `link` fails `EEXIST` at a directory, nothing can read it as a claim, and rule
    1 therefore says "stale" — but the takeover path used to need readable bytes,
    so it never ran: both attempts re-`link`ed into `EEXIST`, `acquire` returned
    `None`, and EVERY later pass reported "another drainer holds this spool" with
    no identity, forever. That is the permanent block #14350 forbids, in its worst
    form (measured in review).

    MUTATION THAT REDS THIS: restore `if existing is None: continue`.
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    (root / "drain.claim").mkdir()
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None, "a directory is not a holder — it must not block the drain"
    claim.release()


def test_a_DANGLING_SYMLINK_at_the_claim_path_is_cleared_not_followed(tmp_path):
    """A symlink is removed as a LINK, so this can never reach outside the spool.

    MUTATION THAT REDS THIS: `path.unlink()` without the symlink branch is fine;
    `path.resolve()` or `os.remove(path.read_bytes())` is not — this pins that the
    removal does not follow the link.
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be touched")
    (root / "drain.claim").symlink_to(outside)
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None, "a symlink is corruption, not a holder"
    assert outside.read_text() == "must not be touched", "the target was followed"
    claim.release()


def test_an_UNCLEARABLE_claim_path_is_reported_UNAVAILABLE_not_silent(tmp_path):
    """When the obstruction cannot be cleared, the drain must SAY so (and run
    unclaimed), never report contention.

    A non-empty directory is the case: deleting unknown content is the worse error,
    so it is left alone — and the caller must then be told "the claim is
    unavailable", because `None` means "another drainer holds it" and the operator
    line would name a holder that does not exist.

    MUTATION THAT REDS THIS: return `None` instead of raising (the pass then skips
    the spool forever while claiming somebody else is draining it).
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    (root / "drain.claim").mkdir()
    (root / "drain.claim" / "keep").write_text("x")
    with pytest.raises(drain_claim.DrainClaimUnavailable):
        drain_claim.acquire(root, leg="python")
    assert (root / "drain.claim" / "keep").exists(), "unknown content must not be deleted"


def test_a_pass_that_RECOVERS_from_a_capacity_refusal_is_not_recorded_BLOCKED(tmp_path):
    """A refusal that the pass then got past is not the box's END state.

    `_flush_claimed` clears `capacity_stopped` before it retries, but `summary.capacity`
    stayed set — and `_record_capture_status` keyed on it, so a pass that yielded
    once and then filed EVERYTHING wrote a durable `blocked` + 429. With anything
    left queued for an unrelated reason, `session health` then rendered
    `capture: BLOCKED (n queued, 429 capture capacity)` — a cause the box no longer
    had (found in review).

    MUTATION THAT REDS THIS: key `_record_capture_status` on `summary.capacity is
    not None` again (drop the `capacity_stopped` gate).
    """
    root = _spool(tmp_path, sessions=3)
    post, _state = _capacity(refusals=1)  # one refusal, then the box is free
    summary = flush_spool(root, post, now=1000.0, sleep=lambda _s: None)
    assert summary.filed == 3, "the pass recovered and filed everything"
    assert summary.capacity_stopped is False
    assert summary.capacity is not None, "the refusal is still MEASURED"
    status = read_capture_status(root)
    assert (status or {}).get("state") != "blocked", (
        f"a recovered pass recorded {status!r} — the box is not blocked")


def test_an_UNREADABLE_regular_claim_is_HONOURED_never_deleted(tmp_path):
    """⛔ The one place this protocol used to fail OPEN.

    A *transient* read error on a perfectly good regular claim file — EMFILE, EIO,
    an NFS hiccup, EACCES after a mode drift — made `_read_raw` answer `None`, and
    `None` routed to "clear the obstruction", which unlinked any regular file it
    was pointed at. So a live holder's claim could be DELETED and handed to a second
    process while the first still believed it owned the spool (found in review, with
    a one-error reproduction), with no attacker needed — only a busy box.

    The doctrine everywhere else here is "unprovable reads as ALIVE"; a claim we
    cannot read is honoured, not cleared.

    MUTATION THAT REDS THIS: route `existing is None` straight to
    `_clear_unusable_claim_path` (drop the `S_ISREG` gate).
    """
    if os.getuid() == 0:
        pytest.skip("root can read a mode-000 file")
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    path = root / "drain.claim"
    path.write_text(json.dumps({
        "version": drain_claim.CLAIM_VERSION, "token": "live-holder", "leg": "pi",
        "pid": os.getpid(), "host": drain_claim._host(),
        "started_at": 0.0,
        "expires_at": time.time() * drain_claim.TIMESTAMP_UNIT_MS + 600_000,
    }))
    os.chmod(path, 0o000)
    try:
        claim = drain_claim.acquire(root, leg="python")
        assert claim is None, (
            "a claim we could not READ was taken over — that is the fail-open "
            "direction: the live holder is still draining")
        assert path.exists(), "a regular claim file must NEVER be deleted unread"
    finally:
        os.chmod(path, 0o600)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
def test_a_FIFO_at_the_claim_path_does_not_HANG_the_drain(tmp_path):
    """Reading a FIFO blocks until a writer appears, so a corrupt object at the
    claim path used to hang every drain, `renew`, `holder` and health surface
    forever — the permanent block #14350 forbids, in the one corruption class the
    code claimed to cover (found in review: measured as a hang, killed at 15 s).

    MUTATION THAT REDS THIS: read the path without the `S_ISREG` gate — this test
    then TIMES OUT rather than asserting (run it under a watchdog to see it).
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    os.mkfifo(root / "drain.claim")
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None, (
        "a FIFO is not a holder and must not block the drain (if this test hung, "
        "the FIFO was READ)")
    claim.release()
    # ⛔ AND THE READER IS TESTED WHILE THE FIFO IS STILL THERE. The first version of
    # this arm read the path AFTER `acquire` had already unlinked the FIFO and after
    # `release` had removed its own claim, so it asserted on an ABSENT path (a
    # tautology: `read_claim` maps FileNotFoundError to None) while the shipped
    # `read_claim` still blocked forever on a FIFO (#14423 review, P2). The read runs
    # under a watchdog so a regression REDS instead of hanging the suite.
    os.unlink(claim_path) if (claim_path := root / "drain.claim").exists() else None
    os.mkfifo(claim_path)
    outcome: list[object] = []

    def _read() -> None:
        outcome.append(drain_claim.read_claim(root))

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(5.0)
    assert not reader.is_alive(), (
        "read_claim BLOCKED on a FIFO — the permanent block #14350 forbids, and it is "
        "what renew/holder/health call")
    assert outcome == [None], f"a FIFO has no claim in it, got {outcome!r}"


# ── The refusal's NAME must be the server's, in BOTH legs ──────────────────

_INGEST_BODY = json.dumps({"detail": {
    "code": "ingest_rate_limited",
    "message": "ingest rate limited",
    "retry_after": 60,
}})


def test_the_severed_SECOND_429_keeps_the_servers_own_NAME(tmp_path):
    """`capture_capacity_saturated` is not the only 429 in the capture lane.

    `hosted_api._ingest_rate_limited` sends `{code: ingest_rate_limited}` for the
    per-ORG ingest window. Relabelling it "capacity" names a cause the server never
    sent, disagrees with the server's own `/health` counter, and writes the wrong
    advice ("wait for a slot" instead of "wait for the org window") into the DURABLE
    status the other leg also reads.

    MUTATION THAT REDS THIS: hardcode `code = CAPTURE_CAPACITY_CODE`.
    """
    _spool(tmp_path, sessions=1)
    outcome = PostOutcome(ok=False, status=429, detail=_INGEST_BODY)
    refusal = capacity_refusal(outcome)
    assert refusal is not None, "a 429 is still a box-level refusal (the CHARGING rule)"
    assert refusal["code"] == "ingest_rate_limited", (
        "the server named this refusal; the class name must not overwrite it")
    assert refusal["retry_after"] == 60.0
    # …and a bare 429 (no typed body) still gets the class name.
    bare = capacity_refusal(PostOutcome(ok=False, status=429, detail=""))
    assert bare is not None and bare["code"] == CAPTURE_CAPACITY_CODE


@pytest.mark.skipif(_NODE is None, reason="node is not installed on this box")
def test_BOTH_legs_name_the_same_429_the_same_way(tmp_path):
    """One refusal body, two legs, ONE answer — the seam the last cycle missed.

    The legs write the SAME `<spool>/status.json`. If they disagree about the
    refusal's name, whichever ran last overwrites the other's record, and the
    operator line names a cause the server did not send.

    MUTATION THAT REDS THIS: hardcode the class name on either leg.
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    script = (
        f'import {{ capacityRefusal }} from {json_dumps(_TS_LEG.as_posix())};\n'
        'const r = capacityRefusal({ status: 429, detail: process.env.BODY });\n'
        'process.stdout.write(JSON.stringify(r ?? null));\n'
    )
    out = subprocess.run([_NODE, "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=120,
                         env={**os.environ, "BODY": _INGEST_BODY})
    assert out.returncode == 0, out.stderr[-2000:]
    ts_refusal = json.loads(out.stdout)
    py_refusal = capacity_refusal(
        PostOutcome(ok=False, status=429, detail=_INGEST_BODY))
    assert ts_refusal is not None and py_refusal is not None
    assert ts_refusal["code"] == py_refusal["code"], (
        f"the legs disagree about the same 429: TS {ts_refusal['code']!r} vs "
        f"Python {py_refusal['code']!r} — whichever writes status.json last wins")


def test_an_EMPTY_only_session_id_is_UNTARGETED_and_takes_the_claim(tmp_path):
    """`""` must not mean "drain the whole spool, unclaimed".

    The claim is taken for an untargeted pass and skipped for a targeted one, and
    the per-entry filter is `if only_session_id and …` — so `""` drains EVERYTHING
    while, before this change, the claim test (`is None`) treated it as TARGETED and
    skipped the claim. One empty string therefore bypassed the one-drainer rule.

    MUTATION THAT REDS THIS: `if only_session_id is None:` for the claim gate.
    """
    root = _spool(tmp_path, sessions=3)
    holder = drain_claim.acquire(root, leg="pi")
    assert holder is not None
    posts: list = []
    summary = flush_spool(root, lambda p: posts.append(p) or PostOutcome(
        ok=True, status=200, body={}), now=1000.0, only_session_id="")
    assert summary.held_by_claim is True, (
        "an empty session id drained a spool another process was draining")
    assert posts == [], "and it posted nothing"
    holder.release()


def test_a_renew_never_overwrites_a_successor_installed_in_the_window(
    tmp_path, monkeypatch,
):
    """R2, the half the read-back could not cover.

    The window the two-drainer defect lives in is between the moment a holder
    decides it may still renew and the moment its bytes land. `renew` used to
    close that window with `os.replace` (which cannot fail on a live target) plus a
    read-back — and the read-back read back THE BYTES THE PROCESS HAD JUST WRITTEN,
    so it reported success while the successor was gone.

    This models the interleaving at the seam: a peer completes a takeover after the
    holder has committed to renewing and before its write. The holder must lose, and
    the SUCCESSOR'S record must be the one on disk afterwards.

    MUTATION THAT REDS THIS: write unconditionally after the takedown (restore the
    `os.replace` semantics) — the first assertion passes, the successor's claim is
    clobbered and the second reds.
    """
    root = tmp_path / "spool"
    claim = drain_claim.acquire(root, leg="python", now=1000.0, ttl_s=60.0)
    assert claim is not None

    real_takedown = drain_claim._takedown
    peer_token = "peer-successor-1234"

    def interleaved(path, token):
        # The window: a peer judged OUR expired record stale and installed its own
        # claim, all after this holder committed to renewing. Written directly (a
        # real `acquire` here would re-enter the very function under test).
        peer_record = {
            "version": drain_claim.CLAIM_VERSION,
            "token": peer_token,
            "leg": "pi",
            "pid": os.getpid(),
            "host": drain_claim._host(),
            "boot_id": drain_claim._boot_id(),
            "started_at": _ms(1061.0),
            "expires_at": _ms(1121.0),
        }
        tmp = path.with_name(f"{path.name}.tmp-peer")
        tmp.write_text(json.dumps(peer_record) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return real_takedown(path, token)

    monkeypatch.setattr(drain_claim, "_takedown", interleaved)
    assert claim.renew(now_s=1010.0) is False, (
        "a holder that finds a successor in its window must lose, not overwrite it")
    monkeypatch.undo()

    on_disk = drain_claim.read_claim(root)
    assert on_disk is not None and on_disk["token"] == peer_token, (
        "the successor's claim must survive the slow holder's renewal")


def test_a_renew_yields_when_a_peer_links_a_claim_in_the_same_window(
    tmp_path, monkeypatch,
):
    """The other side of the same window: the peer's `link` wins the race.

    `renew` installs with `link`, which is create-if-absent. When a peer has
    installed a claim since our takedown, that install MUST fail rather than
    replace — the EEXIST is the successor winning, not an error to retry.

    MUTATION THAT REDS THIS: install with `os.replace` (an unconditional write) —
    the peer's claim disappears and the last assertion reds.
    """
    root = tmp_path / "spool"
    claim = drain_claim.acquire(root, leg="python", now=1000.0, ttl_s=60.0)
    assert claim is not None

    real_link = drain_claim._link_claim_atomic
    seen: dict[str, object] = {}

    def link_after_peer(path, record):
        peer = drain_claim.acquire(root, leg="pi", now=1000.5, ttl_s=60.0)
        assert peer is not None, "the path is free after our takedown"
        seen["peer"] = peer
        return real_link(path, record)  # must raise FileExistsError

    monkeypatch.setattr(drain_claim, "_link_claim_atomic", link_after_peer)
    assert claim.renew(now_s=1010.0) is False
    monkeypatch.undo()

    peer = seen["peer"]
    on_disk = drain_claim.read_claim(root)
    assert on_disk is not None and on_disk["token"] == peer.token
    peer.release()


def test_a_renew_write_failure_keeps_the_lease_and_never_vanishes(
    tmp_path, monkeypatch,
):
    """A failed refresh is not a lost claim, and it must not leave a hole.

    The record is restored (not deleted) when the install raises, so a peer never
    finds the spool unclaimed merely because one `fsync` hit ENOSPC.

    MUTATION THAT REDS THIS: dispose the quarantine on the failure path instead of
    restoring it — the claim file is gone and the second assertion reds.
    """
    root = tmp_path / "spool"
    claim = drain_claim.acquire(root, leg="python", now=1000.0, ttl_s=60.0)
    assert claim is not None

    def failing_link(path, record):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(drain_claim, "_link_claim_atomic", failing_link)
    assert claim.renew(now_s=1010.0) is True, (
        "a write failure keeps the un-refreshed lease: the claim is still ours")
    monkeypatch.undo()
    assert drain_claim.read_claim(root)["token"] == claim.token, (
        "and the record is back on disk, not lost to the failed install")
    claim.release()
