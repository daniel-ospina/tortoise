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


def test_a_reboot_makes_a_same_hosts_claim_stale(tmp_path):
    """A rebooted box can REUSE a pid, and a reused pid is not the holder.

    MUTATION THAT REDS THIS: drop the boot_id comparison (the claim then survives
    a reboot on a pid that now belongs to an unrelated process, and no drain ever
    runs again).
    """
    record = {
        "version": 1, "token": "t", "leg": "pi", "pid": os.getpid(),
        "host": "box-a", "boot_id": "boot-1",
        "started_at": 0.0, "expires_at": time.time() + 600.0,
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
        "version": 1, "token": "t", "leg": "python", "pid": 999_999_999,
        "host": "another-box", "boot_id": "boot-9",
        "started_at": 0.0, "expires_at": 2000.0,
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
    post, state = _capacity(refusals=2)
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
            saturated = self.in_flight > self.limit
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

    Both halves of the shape are exercised together: the claim makes the second
    drainer a no-op, and a capacity refusal (if one happens at all) costs no entry
    anything. The `high_water` assertion is the ceiling's own measurement: a
    serialized drain holds ONE of the eight slots, so a live capture can never be
    starved by it.

    MUTATION THAT REDS THIS: drop the claim (both passes run, the ceiling is hit,
    refusals appear) or charge capacity refusals to entries (attempts climb on
    every saturated pass).
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

    # …and it STAYS there for a full cycle: another pass files nothing, defers
    # nothing, and charges nobody.
    settled = flush_spool(root, server.post, now=time.time() * 1000.0)
    assert settled.filed == 0 and settled.deferred == 0
    assert count_pending_captures(root) == 0
    assert drain_claim.read_claim(root) is None
