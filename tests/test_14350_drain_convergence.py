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
    DEFAULT_BOUNDS,
    DRAIN_CAPACITY_YIELDS,
    Bounds,
    PostOutcome,
    Snapshot,
    _age_key,  # noqa: F401  — the ordering the monotonicity argument rests on
    capacity_refusal,
    capture_health_line,
    count_pending_captures,
    flush_spool,
    read_capture_status,
    read_spool_meta,
    write_capture_status,
    write_spool_entry,
)

REPO = Path(__file__).resolve().parent.parent

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


def _spool_at(root: Path, sessions: int = 3, prefix: str = "sess") -> Path:
    """Materialise `sessions` pending entries at an EXPLICIT root.

    `_spool` below hard-codes `<tmp>/spool`; the cross-leg parity matrix needs one
    spool per row, on both legs, with identical content.
    """
    root.mkdir(parents=True, exist_ok=True)
    for i in range(sessions):
        write_spool_entry(
            root,
            Snapshot(session_id=f"{prefix}-{i}", turns=ROOT_TURNS, source="test",
                     machine_id="machine-1"),
        )
    return root


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


def test_the_claim_is_written_COMPLETE_never_visible_empty(tmp_path, monkeypatch):
    """A reader that sees a zero-length claim reads it as corrupt (rule 1) and
    would STEAL a live claim mid-write.

    ⛔ THE ASSERTION IS THE ORDER, NOT THE RESULT. Asserting the file's contents
    AFTER `acquire` returns cannot see this at all: a create-then-write mutant
    satisfies any post-hoc check, because by then the write has long landed
    (#14423 cycle-7 review, P2 — that is exactly what the earlier version of this
    test did). So the source of the `link` is read AT THE INSTANT the link makes the
    name visible: those are the bytes a concurrent reader would see.

    MUTATION THAT REDS THIS: create the claim at its final path with
    `open(path, O_CREAT|O_EXCL)` and then write it — no `link` happens, so nothing
    is ever "published", and the first assertion fails.
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    published: list[bytes] = []
    real_link = drain_claim.os.link

    def reading_link(src, dst, **kwargs):
        published.append(Path(src).read_bytes())  # the bytes a reader races against
        return real_link(src, dst, **kwargs)

    monkeypatch.setattr(drain_claim.os, "link", reading_link)
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None
    assert published, (
        "the claim must be PUBLISHED by `link` from a fully-written file — a claim "
        "created in place is visible before its bytes are")
    assert json.loads(published[0].decode("utf-8"))["token"] == claim.token, (
        "the first bytes any reader can observe must be a COMPLETE record")
    monkeypatch.undo()
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


def test_a_release_does_not_MOVE_a_claim_that_is_not_ours(tmp_path, monkeypatch):
    """A not-ours release must take NO destructive step at all — not even a rename.

    The takedown-then-compare version `rename`d the successor's claim aside before
    discovering it was not ours; if a THIRD process installed into the now-empty
    path, the restore `link` failed `EEXIST` and the successor's bytes were
    unlinked — a live claim destroyed by a release that was trying to be polite
    (#14423 review, P2).

    ⛔ The first version of this test asserted the file's inode and mtime were
    unchanged, and that CANNOT fail on the order it named: `os.rename` keeps the
    inode and `os.link` restores a second name to the SAME inode, so both hold
    under either order (measured — restoring the pre-fix `release` left the whole
    suite green). What discriminates is whether the takedown was CALLED at all, so
    the assertion is on exactly that.

    MUTATION THAT REDS THIS: take the claim aside before reading its token (the
    takedown then runs for a release that does not own the claim).
    """
    root = tmp_path / "spool"
    slow = drain_claim.acquire(root, leg="python", now=1000.0, ttl_s=60.0)
    assert slow is not None
    successor = drain_claim.acquire(root, leg="pi", now=1100.0, ttl_s=60.0)
    assert successor is not None
    calls: list[str] = []
    real = drain_claim._takedown

    def _counting(path, token):
        calls.append(token)
        return real(path, token)

    monkeypatch.setattr(drain_claim, "_takedown", _counting)
    assert slow.release() is False, "the token is no longer ours"
    assert calls == [], (
        "a release that does not own the claim must not even MOVE it — the takedown "
        "is what opens the window a third process installs into")
    assert drain_claim.read_claim(root)["token"] == successor.token
    # …and an OURS release still takes it, so the guard did not disable the path.
    assert successor.release() is True
    assert calls == [successor.token], calls


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


def test_a_lease_that_LAPSED_OUR_OWN_SCAN_does_not_stall_the_drain(tmp_path, monkeypatch):
    """The pass's one UNBOUNDED step runs inside the lease, so a long scan expired the
    holder's OWN claim — and every later pass then did nothing, forever.

    `renew` refuses an already-expired record on purpose (a holder that overran must
    not overwrite the successor that legitimately took over), but the scan
    (`list_spool_metas`) happens BETWEEN the acquisition and the first refresh, so a
    scan longer than the TTL left the pass unable to renew, `lost_claim`, zero POSTs,
    and the next opportunity repeated it — with a message blaming a peer that does not
    exist. Measured at the production TTL before the fix: two passes, `attempted: 0`,
    `lost_claim: True` (#14423 merge-gate review, runtime finding).

    The lease lapsing is simulated by making the FIRST `renew_if_due` answer `False`
    (the expired-lease answer); `reclaim` then runs for real against a real claim file
    whose token is ours.

    MUTATION THAT REDS THIS: drop the `reclaim` retry at the top of the loop (the pass
    then reports `lost_claim` and attempts nothing).
    """
    root = _spool(tmp_path, sessions=2)
    posts: list[str] = []

    def post(payload):
        posts.append(payload["session_id"])
        return PostOutcome(ok=True, status=200, body={})

    from tortoise.drain_claim import DrainClaim

    real_renew_if_due = DrainClaim.renew_if_due
    calls = {"n": 0}

    def lapsed_once(self, now_s=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return False          # what an expired lease answers
        return real_renew_if_due(self, now_s)

    monkeypatch.setattr(DrainClaim, "renew_if_due", lapsed_once)
    summary = flush_spool(root, post, now=1000.0)
    assert summary.lost_claim is False, (
        "our OWN lease lapsing during our own scan must not be reported as a takeover")
    assert len(posts) == 2, "every entry is still attempted"
    assert summary.filed == 2


def test_a_REAL_takeover_still_stops_a_lapsed_pass(tmp_path, monkeypatch):
    """The other direction, and it is what makes the retry safe: the re-arm only
    happens while the record on disk is OURS. A peer that claimed in the gap wins.

    MUTATION THAT REDS THIS: reclaim without the token check.
    """
    root = _spool(tmp_path, sessions=2)
    posts: list[str] = []

    def post(payload):
        posts.append(payload["session_id"])
        return PostOutcome(ok=True, status=200, body={})

    claim = drain_claim.acquire(root, leg="python", now=1000.0, ttl_s=60.0)
    assert claim is not None
    peer = drain_claim.acquire(root, leg="pi", now=1200.0, ttl_s=60.0)  # the lease expired
    assert peer is not None
    assert claim.reclaim(now_s=1201.0) is False, "a peer's record must never be re-armed"
    assert drain_claim.read_claim(root)["token"] == peer.token
    peer.release()


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
    (the capacity half is INLINE below, in
    `test_a_CAPACITY_refusal_charges_NOTHING_and_stops_the_pass` — an earlier revision
    cited a separate `..._charges_NOBODY` that never existed), which drives the refusal
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

    MUTATION THAT REDS THIS: drop the BYTE COMPARE in `_dispose_quarantine`
    (dispose unconditionally) — `restored_ok` becomes True and the successor's bytes
    are deleted. ⛔ NOT "go back to compare-then-unlink": that reverts `acquire`'s
    TAKEOVER, which this test does not drive, so it would leave every assertion here
    green — the overclaim the Pi twin already records
    (`tortoise-capture.test.ts`, "Reverting to compare-then-unlink does NOT red THIS
    test"). The nearest cover for the acquire path is
    `test_a_crashed_takers_quarantine_is_COLLECTED_by_the_scratch_sweeper` (which
    patches `_dispose_quarantine` to False and reds if `acquire` proceeds — it asserts
    the GIVE-UP, not the restore) and
    `test_a_RENEW_never_clobbers_a_peer_that_claimed_in_the_window` above, which
    drive the window through `_takedown` rather than this primitive.
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


def test_a_DISPLACED_claim_is_PRESERVED_when_its_restore_FAILS(tmp_path, monkeypatch):
    """⛔ THE DISPLACEMENT PATH OBEYS THE SAME RULE AS THE RESTORE (review, cycle 16).

    `_dispose_quarantine` puts a displaced claim BACK with `link` when the bytes are
    not the ones it judged stale. On a non-`EEXIST` failure the quarantined file is
    the ONLY copy of that holder's claim — the rename vacated the path — so unlinking
    it turns "restore failed" into "claim destroyed" and frees the path for the next
    `link`, which is the double-holder window this whole module closes. That branch
    now keeps the object and returns `False` (fail closed: `acquire` answers `None`).

    The Pi leg's row asserts BOTH of its primitives
    (`tortoise-capture.test.ts`, "a FAILED restore does not delete the only copy of a
    peer claim" — `restoreQuarantine` **and** `disposeQuarantine`); this leg's row
    covered only `_restore_quarantine`, so half of the same fix was unpinned here.

    MUTATION THAT REDS THIS: delete the `return False` from `_dispose_quarantine`'s
    `except OSError` arm (the pre-fix code unlinked the quarantine and returned
    `False` either way, so the RETURN VALUE alone cannot tell the two apart — the
    preservation assertion is the whole pin).
    """
    root = tmp_path / "spool"
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None
    path = drain_claim.claim_path(root)
    moved = drain_claim._takedown(path, "contender")
    assert moved is not None, "the fixture never reached the displacement"
    quarantine, data = moved
    assert not path.exists(), "the takedown must vacate the claim path"

    real_link = os.link

    def link_fails(src, dst, *a, **kw):
        if ".tmp-take-" in Path(src).name:
            raise OSError(28, "No space left on device")  # ENOSPC, not EEXIST
        return real_link(src, dst, *a, **kw)

    monkeypatch.setattr(os, "link", link_fails)
    try:
        restored = drain_claim._dispose_quarantine(
            quarantine, data, path, b"<not what was judged stale>")
    finally:
        monkeypatch.undo()
    assert restored is False, "a restore that could not happen must give up this tick"
    assert quarantine.exists(), (
        "the only copy of the displaced claim was DELETED when the restore failed")
    assert quarantine.read_bytes() == data, (
        "the preserved object must be the claim that was displaced, byte for byte")


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


def test_a_pass_that_HELD_the_claim_clears_the_fact_WITHOUT_attempting_anything(tmp_path):
    """⛔ THE PASS THAT ATTEMPTS NOTHING IS EXACTLY THE PASS THAT CLEARS NOTHING —
    which left the durable fact STRANDED on the operator's line.

    The `ok` write is gated on `attempted > 0` (an empty spool must not erase a
    recorded refusal), so a pass holding the claim over an empty or
    backoff-only spool wrote no record at all and the field survived: the operator
    kept reading `NO drain exclusion (<path>)` for an obstruction they had already
    removed, and the self-heal could not fire either. Measured: obstruction → pass 1
    records it; remove it; pass 2 acquires the claim, attempts 0 → the line still
    said "NO drain exclusion".

    MUTATION THAT REDS THIS: gate the clearing on `attempted > 0` too (the field is
    then only ever cleared by a filing pass).
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    write_capture_status(root, {
        "version": 1, "state": "ok",
        "claim_unavailable": True,
        "claim_unavailable_reason": "stale obstruction",
        "updated_at": "2026-01-01T00:00:00+00:00",
    })
    # NO entries: the pass holds the claim and attempts nothing.
    summary = flush_spool(root, lambda _e: PostOutcome(ok=True), now=1000.0,
                          sleep=lambda _s: None)
    assert summary.attempted == 0, "this test is about the pass that attempts NOTHING"
    recorded = read_capture_status(root)
    assert not recorded.get("claim_unavailable"), (
        "a pass that HELD the claim must clear the fact even with nothing to do: "
        + repr(recorded))
    assert "NO drain exclusion" not in capture_health_line(root, api_key="present").line


def test_the_HEALTH_LINE_is_BYTE_IDENTICAL_on_both_legs(tmp_path):
    """⛔ THE CROSS-LEG PIN THE OLD COMMENT PROMISED AND NOBODY WROTE.

    `capture_health_line`'s docstring and the parity test's own prose both said the
    rendered sentence was "pinned by a cross-leg parity test that executes the real
    TypeScript" — but the only cross-leg comparison was the STATUS CLASSIFIERS, and
    the health line had one substring assertion per leg. So changing either leg's
    punctuation, spacing or suffix left both suites green while the two legs told
    an operator two different things about one spool (found in review).

    This executes the real Pi leg over a matrix of SPOOL SHAPES (not just status
    records) and compares its FULL line with the Python leg's, byte for byte.

    ⛔ THE MATRIX REACHES EVERY VERDICT THE SUFFIX MUST SURVIVE (#14480 review,
    F1). The predecessor's `blocked` row carried `queued == 0`, so the self-heal
    rewrote it to `ok` and the row rendered `capture: OK (0 queued)` — it never
    reached the BLOCKED branch at all, which is exactly the branch the durable fact
    was disappearing on. Every row here therefore names the SPOOL it is about
    (`sessions`), the credential (`api_key`) and the ceilings (`max_entries`), and
    the rows cover BLOCKED-with-a-backlog, SATURATED, INACTIVE, DRAINING and
    UNREADABLE — all five with the claim fact present.

    MUTATION THAT REDS THIS: apply the claim term to the final verdict only (the
    pre-fix code) — the blocked/saturated/inactive/unreadable rows lose the suffix
    on BOTH legs, and while parity still holds, `test_every_VERDICT_carries_the…`
    below reds; change either leg's separator (`", NO drain exclusion"`) and THIS
    test reds.
    """
    from tests.test_capture_spool import _node_that_can_strip_ts

    node = _node_that_can_strip_ts()
    ext = (REPO / "tortoise" / "pi-hooks" / "tortoise-capture.ts").as_uri()
    stamp = "2026-01-01T00:00:00+00:00"
    claim = {"claim_unavailable": True,
             "claim_unavailable_reason": "/x/drain.claim exists and is not a claim record"}
    ok = {"version": 1, "state": "ok", "updated_at": stamp}
    blocked = {"version": 1, "state": "blocked", "reason": "402 quota",
               "status": 402, "updated_at": stamp}
    # (label, recorded status.json, spool entries, api key, max_entries, unlistable)
    rows = [
        ("ok", ok, 0, "present", None, False),
        ("ok+claim", {**ok, **claim}, 0, "present", None, False),
        ("blocked-healed+claim", {**blocked, **claim}, 0, "present", None, False),
        ("blocked+backlog+claim", {**blocked, **claim}, 1, "present", None, False),
        ("saturated+claim", {**ok, **claim}, 1, "present", 1, False),
        ("saturated-keyless+claim", {**ok, **claim}, 1, "", 1, False),
        ("inactive+claim", {**ok, **claim}, 1, "", None, False),
        ("draining+claim", {**ok, **claim}, 1, "present", None, False),
        ("unreadable+claim", {**ok, **claim}, 0, "present", None, True),
    ]
    big = 1 << 40
    driver = tmp_path / "health-parity.mjs"
    driver.write_text(
        'import { captureHealthLine } from "' + ext + '";\n'
        "const jobs = JSON.parse(process.argv[2]);\n"
        "const out = [];\n"
        "for (const j of jobs) {\n"
        "  const opts = { apiKey: j.apiKey };\n"
        "  if (j.maxEntries) opts.bounds = { maxEntries: j.maxEntries,\n"
        "    maxTotalBytes: j.big, maxEntryBytes: j.big };\n"
        "  out.push(captureHealthLine(j.dir, opts).line);\n"
        "}\n"
        "console.log(JSON.stringify(out));\n",
        encoding="utf-8",
    )
    jobs = []
    for i, (_label, record, sessions, api_key, max_entries, unlistable) in enumerate(rows):
        for leg in ("pi", "py"):
            root = tmp_path / f"{leg}{i}"
            if sessions:
                _spool_at(root, sessions)
            if unlistable:
                root.mkdir(parents=True, exist_ok=True)
                (root / "entries").write_text("not a directory")
            else:
                root.mkdir(parents=True, exist_ok=True)
            write_capture_status(root, record)
        jobs.append({"dir": str(tmp_path / f"pi{i}"), "apiKey": api_key,
                     "maxEntries": max_entries, "big": big})
    proc = subprocess.run(
        [node, "--experimental-strip-types", str(driver),
         json.dumps(jobs)],
        capture_output=True, text=True, timeout=120, cwd=str(REPO),
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    pi_lines = json.loads(proc.stdout.strip().splitlines()[-1])
    py_lines = []
    for i, (_label, _record, _sessions, api_key, max_entries, _unlistable) in enumerate(rows):
        bounds = (Bounds(max_entries=max_entries, max_total_bytes=big, max_entry_bytes=big)
                  if max_entries else DEFAULT_BOUNDS)
        py_lines.append(capture_health_line(
            tmp_path / f"py{i}", api_key=api_key, bounds=bounds).line)
    # The UNREADABLE verdict names the entries directory it could not list, and the
    # two legs necessarily run on two different ones — normalise ONLY the spool
    # root, so every other byte of every row is still compared exactly.
    def _norm(lines: list[str]) -> list[str]:
        return [ln.replace(str(tmp_path / "pi"), "<root>").replace(
            str(tmp_path / "py"), "<root>") for ln in lines]

    assert _norm(pi_lines) == _norm(py_lines), (
        "the two legs describe the SAME spool with different words:\n  pi: "
        + repr(pi_lines) + "\n  py: " + repr(py_lines))
    # ⛔ AND EVERY ROW ACTUALLY REACHED A VERDICT WITH THE FACT ON IT: a matrix of
    # rows that all collapse to `ok` is a parity test for one branch (the shape the
    # predecessor had). The expected head-word per row is derived from the row, so a
    # row that silently takes another branch fails HERE rather than passing by
    # agreeing on the wrong sentence.
    expected = [("OK", False), ("OK", True), ("OK", True), ("BLOCKED", True),
                ("SATURATED", True), ("SATURATED", True), ("INACTIVE", True),
                ("DRAINING", True), ("UNREADABLE", True)]
    for (label, _r, _s, _k, _m, _u), (verb, fact), line in zip(
            rows, expected, py_lines, strict=True):
        assert line.startswith(f"capture: {verb}"), (
            f"row {label!r} took a different branch than the matrix intends: {line!r}")
        assert line.count("NO drain exclusion") == (1 if fact else 0), (
            f"row {label!r} must carry the durable claim fact exactly "
            f"{1 if fact else 0}x: {line!r}")


def test_every_VERDICT_carries_the_durable_claim_fact(tmp_path):
    """⛔ THE HEADLINE CLAIM OF THIS PR, ASSERTED DIRECTLY (#14480 review, F1).

    `status.json` is written with BOTH facts and the firing path DISCARDS stderr
    (`nohup … >/dev/null 2>&1 &`), so the health line is the only surface through
    which an operator can learn that the drain exclusion is off. It was applied to
    the final `DRAINING`/`OK` verdict only, so on four of six verdicts the fact was
    invisible — including the shape the PR exists for: a CAPACITY 429 with a
    backlog (`state: blocked`) whose claim path is broken reads `BLOCKED` and
    nothing else, for the whole duration of the block.

    MUTATION THAT REDS THIS: `return verdict._replace(line=...)` on the last branch
    only (the pre-fix code) — the BLOCKED, SATURATED, INACTIVE and UNREADABLE
    rows lose the suffix.
    """
    stamp = "2026-01-01T00:00:00+00:00"
    claim = {"claim_unavailable": True, "claim_unavailable_reason": "/x/drain.claim"}
    blocked = {"version": 1, "state": "blocked", "reason": CAPTURE_CAPACITY_CODE,
               "status": 429, "updated_at": stamp}
    big = 1 << 40
    small = Bounds(max_entries=1, max_total_bytes=big, max_entry_bytes=big)
    shapes = [
        ("ok", 0, "present", DEFAULT_BOUNDS, False),
        ("blocked", 1, "present", DEFAULT_BOUNDS, False),
        ("saturated", 1, "present", small, False),
        ("inactive", 1, "", DEFAULT_BOUNDS, False),
        ("draining", 1, "present", DEFAULT_BOUNDS, False),
        ("unreadable", 0, "present", DEFAULT_BOUNDS, True),
    ]
    for i, (label, sessions, api_key, bounds, unlistable) in enumerate(shapes):
        root = tmp_path / f"s{i}"
        if sessions:
            _spool_at(root, sessions)
        if unlistable:
            root.mkdir(parents=True, exist_ok=True)
            (root / "entries").write_text("not a directory")
        else:
            root.mkdir(parents=True, exist_ok=True)
        write_capture_status(root, ({**blocked, **claim} if label == "blocked"
                                    else {"version": 1, "state": "ok", **claim,
                                          "updated_at": stamp}))
        line = capture_health_line(root, api_key=api_key, bounds=bounds).line
        assert "NO drain exclusion (/x/drain.claim)" in line, (
            f"the {label} verdict dropped the durable claim fact: {line!r}")


def test_a_peer_RELEASING_during_the_clear_is_RETRIED_not_called_unclearable(tmp_path):
    """⛔ `GONE` IS NOT `REFUSED` — the distinction a boolean could not make, and
    collapsing them ran a whole pass UNCLAIMED beside a live peer claim.

    Two legs meet at a FIFO at `<spool>/drain.claim`: this one looks at it, a peer
    `unlink`s it (its own release or takeover), and this one's removal step then
    answers `ENOENT`. The old `except OSError: return False` read that as "cannot be
    cleared", `acquire` raised `DrainClaimUnavailable`, and `flush_spool`'s handler
    ran the entire pass without the exclusion — while the peer's own claim sat on
    disk. For the length of a pass the spool had two drainers, which is the defect
    this module exists to remove (and it is NOT the documented `ttl/3` bound: an
    unclaimed pass never holds or re-checks a lease).

    The FIFO stands in for "something that is not a claim, that a peer removes
    under me", and the peer's `unlink` is played by the `os.rename` into quarantine
    failing `ENOENT` — the exact errno, reached the exact way (the removal is a
    `rename` since #14480: `unlink` by name could delete a claim a peer installed in
    the window).

    MUTATION THAT REDS THIS: answer `_CLEAR_REFUSED` (or `False`) from the
    `FileNotFoundError` arm — the FIFO path then raises `DrainClaimUnavailable` and
    the pass runs unclaimed beside the peer.
    """
    if not hasattr(os, "mkfifo"):
        pytest.skip("no mkfifo on this platform")
    root = _spool(tmp_path, sessions=1)
    fifo = drain_claim.claim_path(root)
    os.mkfifo(fifo)

    real_rename = os.rename
    undone: list = []

    def peer_wins(src, dst, *a, **kw):
        if str(src) == str(fifo) and not undone:
            undone.append(src)
            os.unlink(src)                          # the peer's removal lands first
            raise FileNotFoundError(2, "No such file or directory", str(src))
        return real_rename(src, dst, *a, **kw)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(os, "rename", peer_wins)
    try:
        claim = drain_claim.acquire(root, leg="python")
    finally:
        monkeypatch.undo()
    assert undone, "the test never reached the FIFO clear"
    assert claim is not None, (
        "a peer that removed the obstruction is not an unclearable path: `acquire` "
        "must RETRY and install its claim (or honour whatever the peer put there)")
    claim.release()


def test_the_clear_NEVER_deletes_an_object_it_did_not_judge(tmp_path):
    """The obstruction clear is a delete of something that is NOT ours, so it goes
    through the same atomic arbiter as every other removal: the object is MOVED
    ASIDE and the object that landed in the quarantine is what gets judged.

    ⛔ THE PREDECESSOR OF THIS TEST DROVE AN `lstat` SEAM and asserted `REFUSED`
    when the identity changed — narrowing the check-then-unlink window without
    closing it, and pinning the wrong answer while doing so (#14480 review, F2 and
    P2-4): between the re-read and the `unlink` a peer can still install its claim,
    and `unlink` deletes by NAME. An inode compare is not sound evidence either —
    ext4/xfs reuse a freed inode number, so "same dev+ino" can be a different live
    lockfile (`filelock` GHSA-fq56-3cx3-fjxp, two holders).

    So the interleave driven here is the REAL one: a peer replaces the FIFO with its
    own VALID claim in the window between the caller's look and the removal. The
    object that lands in the quarantine is then a regular file, i.e. a claim, and it
    must be put BACK and reported as CONTENTION — never deleted, and never reported
    as an unclearable path (which ran a whole pass unclaimed beside it).

    MUTATION THAT REDS THIS: unlink/quarantine-delete without judging what landed
    (drop the `landed is not None` arm, or restore the old `os.unlink(path)`).
    """
    if not hasattr(os, "mkfifo"):
        pytest.skip("no mkfifo on this platform")
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    fifo = root / "drain.claim"
    os.mkfifo(fifo)
    peer_raw = json.dumps({
        "version": drain_claim.CLAIM_VERSION, "token": "peer-token", "leg": "pi",
        "pid": os.getpid(), "host": drain_claim._host(), "started_at": 0.0,
        "expires_at": time.time() * drain_claim.TIMESTAMP_UNIT_MS + 600_000,
    })
    real_rename = os.rename
    installed: list = []

    def peer_installs_first(src, dst, *a, **kw):
        if str(src) == str(fifo) and not installed:
            installed.append(src)
            real_unlink = os.unlink
            real_unlink(src)                 # the peer's release lands first …
            Path(src).write_text(peer_raw)   # … and its claim is in the path
        return real_rename(src, dst, *a, **kw)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(os, "rename", peer_installs_first)
    try:
        outcome = drain_claim._clear_unusable_claim_path(fifo)
    finally:
        monkeypatch.undo()
    assert installed, "the test never reached the removal"
    assert outcome == drain_claim._CLEAR_HELD, (
        "a claim that appears in the window is CONTENTION, not an unclearable "
        "path — got " + repr(outcome))
    assert fifo.exists(), "the live claim this call did not judge was deleted"
    assert json.loads(fifo.read_text())["token"] == "peer-token", (
        "the peer's claim must be restored BYTE for byte")


def test_the_clear_HONOURS_a_landed_REGULAR_file_it_could_not_READ(tmp_path):
    """`landed` is `None` for TWO different facts, and only one of them says
    "drop it": *not a regular file* (a symlink/FIFO/socket/device — corruption) and
    *a regular file whose bytes could not be had* (`EMFILE`, `EIO`, `EACCES` after a
    mode drift).

    The second is a PEER'S CLAIM that arrived in the window. `unlink`ing it lets
    this process win the next `link`, so two holders coexist for up to `ttl/3` —
    silently, which is exactly the class the `filelock` advisory
    GHSA-fq56-3cx3-fjxp describes. It also inverts the doctrine `acquire` states
    forty lines above: a regular file we could not read is HONOURED and never
    unlinked (#14480 review, F3).

    The interleave is the real one (a peer installs a valid claim in the removal
    window); only the READ of the quarantined object is made to fail, which is the
    transient-error case that cannot be provoked portably on a live box.

    MUTATION THAT REDS THIS: treat `None` from `_read_raw` as "not a claim" and fall
    through to `os.unlink(quarantine)` — the outcome becomes `_CLEAR_DONE` and the
    peer's claim is gone.
    """
    if not hasattr(os, "mkfifo"):
        pytest.skip("no mkfifo on this platform")
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    fifo = root / "drain.claim"
    os.mkfifo(fifo)
    peer_raw = json.dumps({
        "version": drain_claim.CLAIM_VERSION, "token": "peer-token", "leg": "pi",
        "pid": os.getpid(), "host": drain_claim._host(), "started_at": 0.0,
        "expires_at": time.time() * drain_claim.TIMESTAMP_UNIT_MS + 600_000,
    })
    real_rename = os.rename
    installed: list = []

    def peer_installs_first(src, dst, *a, **kw):
        if str(src) == str(fifo) and not installed:
            installed.append(src)
            os.unlink(src)                  # the peer's release lands first …
            Path(src).write_text(peer_raw)  # … and its claim is in the path
        return real_rename(src, dst, *a, **kw)

    real_read_raw = drain_claim._read_raw

    def unreadable_in_quarantine(target):
        # A transient failure on a perfectly good REGULAR file — never evidence
        # that the file is junk.
        if ".tmp-clear-" in Path(target).name:
            return None
        return real_read_raw(target)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(os, "rename", peer_installs_first)
    monkeypatch.setattr(drain_claim, "_read_raw", unreadable_in_quarantine)
    try:
        outcome = drain_claim._clear_unusable_claim_path(fifo)
    finally:
        monkeypatch.undo()
    assert installed, "the test never reached the removal"
    assert outcome == drain_claim._CLEAR_HELD, (
        "a regular file we could not read is a possible HOLDER, not junk — got "
        + repr(outcome))
    assert fifo.exists(), "a live claim this call could not judge was unlinked"
    assert fifo.read_text() == peer_raw, (
        "the peer's claim must be back in the path, byte for byte")


def test_a_peer_claim_landing_in_the_clear_window_is_NOT_an_unclaimed_pass(tmp_path):
    """⛔ THE DIAGNOSIS, NOT JUST THE ANSWER (F2). `acquire` used to answer
    `REFUSED` when the object changed under the clear, which raised
    `DrainClaimUnavailable("… the spool root is not writable by this process")` —
    a FALSE diagnosis (the root was writable, the object had moved) that
    `flush_spool` read as "run the whole pass WITHOUT the one-drainer exclusion"
    while a peer's claim sat on disk.

    End to end: a FIFO at the claim path, and a peer installs a VALID claim in the
    window the clear opens. The only correct answers are "somebody holds it" and
    "that claim still exists" — not an unclaimed pass, and not a deletion.

    MUTATION THAT REDS THIS: answer `_CLEAR_REFUSED` (or delete the object) from the
    `landed is not None` arm.
    """
    if not hasattr(os, "mkfifo"):
        pytest.skip("no mkfifo on this platform")
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    fifo = drain_claim.claim_path(root)
    os.mkfifo(fifo)
    peer_raw = json.dumps({
        "version": drain_claim.CLAIM_VERSION, "token": "peer-token", "leg": "pi",
        "pid": os.getpid(), "host": drain_claim._host(), "started_at": 0.0,
        "expires_at": time.time() * drain_claim.TIMESTAMP_UNIT_MS + 600_000,
    })
    real_rename = os.rename
    installed: list = []

    def peer_installs_first(src, dst, *a, **kw):
        if str(src) == str(fifo) and not installed:
            installed.append(src)
            os.unlink(src)
            Path(src).write_text(peer_raw)
        return real_rename(src, dst, *a, **kw)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(os, "rename", peer_installs_first)
    try:
        claim = drain_claim.acquire(root, leg="python")
    finally:
        monkeypatch.undo()
    assert installed, "the test never reached the clear"
    assert claim is None, (
        "a peer's claim that landed in the window must be HONOURED: `acquire` "
        "answering a claim ran the pass UNCLAIMED beside it")
    assert fifo.exists(), "the peer's live claim was deleted"
    held = drain_claim.read_claim(root)
    assert held is not None and held.get("token") == "peer-token", (
        f"the peer's claim must still be readable at the path: {held!r}")


def test_an_UNCLEARABLE_obstruction_runs_the_pass_UNCLAIMED_and_SAYS_what_it_is(tmp_path):
    """A non-empty directory at the claim path cannot be a CLAIM, so reporting
    contention over it names a holder that does not exist — and wedges the spool
    forever, which #14350's own rule forbids.

    Three records say so and none of them was honoured: `DrainClaimUnavailable`'s
    docstring ("run unclaimed, and SAY so"), the test above ("never report
    contention"), and the issue's "a stale claim is re-verifiable, not a permanent
    block". `acquire` correctly refuses to delete unknown content; the CALLER then
    collapsed that refusal into `held_by_claim` whenever anything was at the path,
    so `claim_unavailable` was unreachable in exactly the case it exists for
    (review finding — measured: three consecutive passes, `attempted=0`, forever).

    MUTATION THAT REDS THIS: test `claim_path(root).exists()` instead of
    `stat.S_ISREG(mode)` in the `DrainClaimUnavailable` arm — the pass then reports
    a holder and files nothing.
    """
    root = _spool(tmp_path, sessions=1)
    obstruction = root / "drain.claim"
    obstruction.mkdir()
    (obstruction / "keep").write_text("x")
    summary = flush_spool(root, lambda p: PostOutcome(ok=True, status=200, body={}),
                          now=1000.0)
    assert summary.held_by_claim is False, (
        "a directory is not a holder: reporting contention names a holder that "
        "does not exist")
    assert summary.claim_unavailable is True
    assert summary.claim_unavailable_reason, "the operator line must name the cause"
    assert "drain.claim" in summary.claim_unavailable_reason
    assert summary.filed == 1, (
        "the pass must actually RUN unclaimed — refusing forever is the permanent "
        "block #14350 forbids")
    assert (obstruction / "keep").exists(), "unknown content must not be deleted"


def test_an_UNREADABLE_regular_claim_still_reads_as_CONTENTION(tmp_path):
    """…and the OTHER half of the same decision: a REGULAR claim file we could not
    read may belong to a live holder, so it is honoured — the fail-closed direction.

    The two arms must stay distinct: collapsing them is what made an unclearable
    DIRECTORY report a phantom holder, and collapsing them the other way would run
    a second drainer beside a live one.

    MUTATION THAT REDS THIS: drop the `stat.S_ISREG` branch (an unreadable regular
    claim then runs the pass unclaimed, beside the holder).
    """
    if os.getuid() == 0:
        pytest.skip("root ignores mode bits")
    root = _spool(tmp_path, sessions=1)
    path = drain_claim.claim_path(root)
    path.write_text(json.dumps({
        "version": drain_claim.CLAIM_VERSION, "token": "live-holder", "leg": "pi",
        "pid": os.getpid(), "host": drain_claim._host(),
        "started_at": 0.0,
        "expires_at": time.time() * drain_claim.TIMESTAMP_UNIT_MS + 600_000,
    }))
    os.chmod(path, 0o000)
    try:
        summary = flush_spool(root, lambda p: PostOutcome(ok=True, status=200, body={}),
                              now=1000.0)
    finally:
        os.chmod(path, 0o600)
    assert summary.held_by_claim is True, (
        "a regular claim we could not read may be a LIVE holder — honour it")
    assert summary.claim_unavailable is False
    assert summary.filed == 0, "no POST may be attempted beside a possible holder"
    assert path.exists(), "a regular claim file must NEVER be deleted unread"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
def test_renew_does_not_HANG_on_a_FIFO_at_the_claim_path(tmp_path):
    """A live holder's REFRESH must not block either — the half the previous test
    could not see, because it never renewed with the FIFO in place.

    `renew` is called once per entry by `renew_if_due`, so a hang here wedges a
    draining pass forever while it still holds the claim: the worst shape of the
    permanent block, because the spool is locked too.

    MUTATION THAT REDS THIS: read the claim with `Path.read_text` instead of
    `_read_raw` — this test then TIMES OUT rather than asserting.
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None
    path = drain_claim.claim_path(root)
    os.unlink(path)
    os.mkfifo(path)
    try:
        assert claim.renew() is False, (
            "a claim we cannot READ must read as 'not ours' — never as a hang")
        # …and the HOT PATH (`renew_if_due`, once per entry). `now` is pushed past
        # the ttl/3 gate so the renewal is actually DUE; without that it returns
        # True without reading, which would make this half vacuous.
        assert claim.renew_if_due(time.time() + 1000.0) is False
    finally:
        os.unlink(path)


def test_a_RENEW_never_clobbers_a_peer_that_claimed_in_the_window(tmp_path, monkeypatch):
    """The refresh must be a COMPARE-AND-SWAP, not a read-then-replace.

    `renew` reads the record and then installs a refreshed one. If a peer's
    takeover lands in that window — which happens exactly when a renewal lands at
    the deadline, after a freeze or a run of failed writes — a replace CLOBBERS
    the peer's fresh claim and `renew` returns True while two drainers exist. The
    earlier version tried to cover this by reading back after the write, which
    reads the bytes it just wrote and so always matched.

    The window is reproduced deterministically: the taker (the same `_takedown`
    `acquire` uses) runs for real, and a peer's claim is installed before `renew`
    gets to its install step.

    MUTATION THAT REDS THIS: install the refresh with `os.replace` instead of
    `os.link` (and drop the `FileExistsError` arm) — `renew` then returns True and
    the on-disk token is ours, not the peer's.
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    claim = drain_claim.acquire(root, leg="python")
    assert claim is not None
    peer = {
        "version": drain_claim.CLAIM_VERSION, "token": "peer-token", "leg": "pi",
        "pid": os.getpid(), "host": drain_claim._host(),
        "boot_id": drain_claim._boot_id(),
        "started_at": 0.0,
        "expires_at": time.time() * drain_claim.TIMESTAMP_UNIT_MS + 600_000,
    }
    real_takedown = drain_claim._takedown

    def takedown_then_peer_wins(p, token):
        moved = real_takedown(p, token)
        # ⛔ THE WINDOW: renew has moved our record aside; a peer legitimately
        # claims the spool before renew installs its refresh.
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(peer) + "\n")
        return moved

    monkeypatch.setattr(drain_claim, "_takedown", takedown_then_peer_wins)
    assert claim.renew() is False, (
        "renew claimed ownership while a peer held a live claim — two drainers")
    on_disk = drain_claim.read_claim(root)
    assert on_disk is not None and on_disk.get("token") == "peer-token", (
        f"the peer's live claim was destroyed by our refresh: {on_disk}")


def test_an_UNCLAIMED_pass_RECORDS_it_DURABLY_and_the_LINE_says_so(tmp_path):
    """⛔ "RUN UNCLAIMED AND SAY SO" IS NOT SATISFIED BY A CONSOLE LINE.

    The primary firing path discards stderr (`session-start.sh`: `nohup … session
    drain … >/dev/null 2>&1 &`), so a pass that ran without the one-drainer
    exclusion and only WARNED left the box reporting `capture: OK`/`DRAINING` while
    N drainers POSTed into the same ceiling — the silent-failure shape this spool's
    rules exist to remove (#14423 cycle-7 review, P1).

    This pass files NOTHING (the spool is empty), so every other rule in
    `_record_capture_status` writes nothing at all: the claim axis has to write the
    record ITSELF.

    MUTATION THAT REDS THIS: drop the `summary.claim_unavailable` arm from
    `_record_capture_status` — no `status.json` is written, and the health line
    reports a normal drain.
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    obstruction = drain_claim.claim_path(root)
    obstruction.mkdir()
    (obstruction / "keep").write_text("not a claim\n", encoding="utf-8")
    summary = flush_spool(root, lambda _e: PostOutcome(ok=True), now=1000.0,
                          sleep=lambda _s: None)
    assert summary.claim_unavailable is True
    recorded = read_capture_status(root)
    assert recorded is not None, "the exclusion being off must be DURABLE"
    assert recorded.get("claim_unavailable") is True
    assert "not a claim record" in recorded["claim_unavailable_reason"]
    line = capture_health_line(root, api_key="present").line
    assert "NO drain exclusion" in line, line
    assert "not a claim record" in line, (
        "the line must name WHAT stops the exclusion: " + line)


def test_a_HEALTHY_pass_CLEARS_the_recorded_claim_fact(tmp_path):
    """The other direction: a durable field nobody clears is a one-way latch, and a
    one-way latch is how an operator learns to ignore the line.

    MUTATION THAT REDS THIS: delete the `elif held_claim:` clearing arm
    (`capture_spool.py`, the arm that pops `claim_unavailable` /
    `claim_unavailable_reason`). ⛔ NOT "keep the stale field when a claimed pass
    writes `ok` / merge the previous record into every write": this pass FILES an
    entry, so it holds the claim AND attempted something, and the clearing arm pops
    the fields again after any merge — measured GREEN under that mutant, which made
    the earlier sentence an unattributable catch. The Pi twin reaches the same VERDICT
    by a DIFFERENT route — its spool is EMPTY on purpose, so `attempted == 0` and that
    `ok` write never executes at all — so its reasoning cannot simply be borrowed: two
    legs, two mechanisms, one conclusion (#14480 review, cycle 12 F3 / cycle 14 P3).
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    write_capture_status(root, {
        "version": 1, "state": "ok",
        "claim_unavailable": True,
        "claim_unavailable_reason": "stale obstruction",
        "updated_at": "2026-01-01T00:00:00+00:00",
    })
    _spool(tmp_path, sessions=1)
    summary = flush_spool(root, lambda _e: PostOutcome(ok=True), now=1000.0,
                          sleep=lambda _s: None)
    assert summary.claim_unavailable is not True
    recorded = read_capture_status(root)
    assert not recorded.get("claim_unavailable"), (
        "a pass that HELD the claim must clear the fact: " + repr(recorded))
    assert "NO drain exclusion" not in capture_health_line(root, api_key="present").line


def test_a_TARGETED_pass_cannot_erase_the_durable_claim_fact(tmp_path):
    """⛔ ONLY A PASS THAT HELD THE CLAIM MAY CLEAR IT (#14480 review, F1).

    A TARGETED pass (`only_session_id` set) takes NO claim by design, so it reports
    neither `claim_unavailable` nor `held_claim` — and it wrote its `ok` record
    WITHOUT the claim fields, which DELETED the durable fact. That pass is the
    SessionEnd path (`tortoise session capture`), the most frequent writer on the
    box; on an obstructed spool one successful capture therefore wiped the warning
    until the next general pass re-recorded it, which is exactly the false-PASS
    direction the durable fact exists to remove.

    MUTATION THAT REDS THIS: gate the carry-forward arm on nothing (i.e. let the
    `attempted > 0` `ok` write replace the record wholesale, as before) — the fact
    is gone from `status.json` and from the health line.
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    write_capture_status(root, {
        "version": 1, "state": "ok",
        "claim_unavailable": True,
        "claim_unavailable_reason": "the spool root cannot hold a claim",
        "updated_at": "2026-01-01T00:00:00+00:00",
    })
    entry = write_spool_entry(
        root,
        Snapshot(session_id="targeted-0", turns=ROOT_TURNS, source="test",
                 machine_id="machine-1"),
    )
    assert entry, "the entry was written"
    summary = flush_spool(root, lambda _e: PostOutcome(ok=True), now=1000.0,
                          only_session_id="targeted-0", sleep=lambda _s: None)
    assert summary.attempted > 0, "the precondition: the targeted pass FILED something"
    assert summary.claim_unavailable is not True
    recorded = read_capture_status(root)
    assert recorded is not None
    assert recorded.get("claim_unavailable") is True, (
        "a targeted pass never asked for the claim, so it cannot clear the fact: "
        + repr(recorded))
    assert recorded.get("claim_unavailable_reason") == (
        "the spool root cannot hold a claim")
    line = capture_health_line(root, api_key="present").line
    assert "NO drain exclusion" in line, line
    assert summary.filed > 0, "the pass still filed its entry; the fact is orthogonal"


def test_a_TARGETED_pass_that_attempted_NOTHING_leaves_the_record_alone(tmp_path):
    """The other half of the same rule: a targeted pass with an empty spool writes
    nothing at all, so it can neither clear nor invent the fact.

    MUTATION THAT REDS THIS: write an `ok` record unconditionally from the targeted
    arm (dropping the `attempted > 0` gate) — `updated_at` moves and the state the
    previous pass recorded is overwritten with a pass that observed nothing.
    """
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    before = {
        "version": 1, "state": "blocked", "reason": "quota_exhausted",
        "claim_unavailable": True,
        "claim_unavailable_reason": "the spool root cannot hold a claim",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    write_capture_status(root, before)
    summary = flush_spool(root, lambda _e: PostOutcome(ok=True), now=1000.0,
                          only_session_id="absent-session", sleep=lambda _s: None)
    assert summary.attempted == 0
    assert read_capture_status(root) == before, (
        "a pass that attempted nothing observed nothing, so it writes nothing")


def test_the_claim_policy_agrees_on_every_summary_shape(tmp_path):
    """⛔ THE TWO LEGS ARE ONE PROTOCOL on a SHARED `<spool>/status.json`, so the
    clearing/carrying rule is compared rather than each leg holding its own opinion.

    This matrix is the SAME one the Pi leg drives
    (`the claim policy agrees between both legs on every summary shape` in
    `tortoise-capture.test.ts`), row for row; a leg that drifts reds on its own side.

    MUTATION THAT REDS THIS: make any row disagree with the twin — clear the fact on a
    pass that did not hold the claim, or write a record from a targeted pass that
    observed nothing.
    """
    from tortoise.capture_spool import FlushSummary, _record_capture_status

    fact = {
        "version": 1, "state": "ok",
        "claim_unavailable": True, "claim_unavailable_reason": "obstructed",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    # (label, attempted, claim state, expect a record?, expect claim_unavailable)
    matrix = [
        ("targeted, earned nothing", 0, {}, False, None),
        ("targeted, earned ok", 3, {}, True, True),
        ("holder, earned nothing", 0, {"held": True}, True, False),
        ("holder, earned ok", 3, {"held": True}, True, False),
        ("unclaimed, earned nothing", 0, {"unavailable": True}, True, True),
        ("unclaimed AND holder", 0, {"unavailable": True, "held": True}, True, True),
        # ⛔ THE EMPTY-REASON ROW (review, cycle 14, P3). An empty reason is NO
        # reason: both legs must fall back to the same default, because they write the
        # same `<spool>/status.json`. This leg tested truthiness (so "" defaulted) and
        # the Pi leg tested nullish (so "" was written verbatim) — the exact predicate
        # class `client_captured_at_source` was fixed for in this same file, and no row
        # here carried `""`, which is why five rounds of parity work missed it.
        ("unclaimed, reason is EMPTY", 0, {"unavailable": True, "empty_reason": True},
         True, True),
        # ⛔ THE FALSE-POSITIVE DIRECTION (review, cycle 15 P2). Every other row
        # starts from an EXISTING fact, so "carry it forward" could be widened to
        # "always stamp it" (`elif record is not None:`) with this matrix green — and
        # a healthy box would then report `NO drain exclusion` forever. This is the
        # only row with NO prior fact, which is what makes inventing one visible.
        ("targeted, earned ok, NO prior fact", 3, {"no_prior": True}, True, False),
    ]
    for index, (label, attempted, state, writes, expected) in enumerate(matrix):
        root = tmp_path / f"row-{index}"
        root.mkdir(parents=True)
        if not state.get("no_prior"):
            write_capture_status(root, dict(fact))
        summary = FlushSummary(attempted=attempted)
        if state.get("held"):
            summary.claim_unavailable = False
        if state.get("unavailable"):
            summary.claim_unavailable = True
            summary.claim_unavailable_reason = (
                "" if state.get("empty_reason") else "obstructed")
        _record_capture_status(
            root, summary,
            held_claim=bool(state.get("held")),
        )
        recorded = read_capture_status(root)
        assert recorded is not None, label + ": prior record vanished"
        wrote = recorded != fact
        assert wrote is writes, label + " → writes? " + repr(recorded)
        if writes:
            assert bool(recorded.get("claim_unavailable")) is expected, (
                label + " → " + repr(recorded))
        if writes and state.get("empty_reason"):
            assert recorded.get("claim_unavailable_reason") == (
                "the spool root cannot hold a claim"), (
                "an EMPTY reason is no reason on BOTH legs, not empty on one: "
                + repr(recorded))


def _receipt(**race) -> dict:
    return {"claim_race": dict(race)}


def test_the_measurement_is_NO_VERDICT_when_NOBODY_won_the_claim():
    """⛔ A RACE NOBODY WON MEASURED NOTHING, and it used to exit 0 as a PASS.

    `supported` was `bounded` alone — a statement about the probe's own hold — so a
    run whose processes never reached their attempt reported `winners: 0` with
    `bounded: true` and `supported: true`, and `main` returned 0. The `BARREN` marker
    that says exactly this was printed by nobody who parsed it (#14480 review, F2).

    MUTATION THAT REDS THIS: drop the `winners == 0` row from `_no_verdict` (the
    pre-fix behaviour: `bounded` is the only question asked).
    """
    import tools.measure_drain_convergence as M

    assert M._no_verdict(_receipt(supported=False, bounded=True, winners=0,
                                  processes=10)) is True
    assert M._no_verdict(_receipt(supported=False, bounded=True, winners=0,
                                  barren=4, processes=10)) is True


def test_the_measurement_is_NO_VERDICT_when_a_process_never_arrived():
    """A `BARREN` process is ABSENT from the race, not a loser: its arrival barrier
    expired, so it never attempted. Counting it as "not a winner" is how a run that
    measured nothing read as a clean one.

    MUTATION THAT REDS THIS: drop the `barren > 0` row from `_no_verdict`.
    """
    import tools.measure_drain_convergence as M

    assert M._no_verdict(_receipt(supported=False, bounded=True, winners=1,
                                  barren=1, processes=10)) is True


def test_the_measurement_is_NO_VERDICT_when_the_winner_RELEASED_early():
    """⛔ THE THIRD WAY OF MEASURING NOTHING, and the one no case exercised.

    Every other case in this family passes `bounded=True`, so the `bounded is False`
    row of `_no_verdict` could be deleted with the suite still green (#14480 review,
    cycle 15 P2 — the round that added it, not cycle 13, whose two P2s are the
    `processes - 1` hold and the Pi leg's `held` wiring). It is not cosmetic: a `bounded: false, winners: 1, barren: 0`
    receipt then exits 0 — byte-indistinguishable from a real two-drainer
    FINDING, which is exactly the fail-open this field exists to close.

    MUTATION THAT REDS THIS: drop the `race.get("bounded") is False` row from
    `_no_verdict`.
    """
    import tools.measure_drain_convergence as M

    assert M._no_verdict(_receipt(supported=False, bounded=False, winners=1,
                                  barren=0, processes=10)) is True


def test_the_measurement_is_NO_VERDICT_when_a_NESTED_race_measured_nothing():
    """⛔ `--scenario all` puts the claim race only NESTED (`baseline` /
    `saturated`), so a scan of the TOP-LEVEL receipt alone never sees it.

    Deleting the nested scan left `_no_verdict` permanently `False` in the tool's
    only multi-race mode — exit 0 on a barren run (#14480 review, cycle 15 P3).
    The loop is what makes the mode's verdict exist at all.

    MUTATION THAT REDS THIS: delete the `for key in ("baseline", "saturated")`
    loop from `_no_verdict` (read `receipt["claim_race"]` only).
    """
    import tools.measure_drain_convergence as M

    for key in ("baseline", "saturated"):
        nested = {key: {"claim_race": {"supported": False, "bounded": True,
                                       "winners": 1, "barren": 2, "processes": 10}}}
        assert M._no_verdict(nested) is True, key + ": " + repr(nested)
        # ⛔ And the containment check is BIDIRECTIONAL: a clean nested race is a
        # real measurement, so the loop must not turn every nested receipt into a
        # no-verdict — that would make `--scenario all` unreadable instead of safe.
        clean = {key: {"claim_race": {"supported": True, "bounded": True,
                                       "winners": 1, "barren": 0, "processes": 10}}}
        assert M._no_verdict(clean) is False, key + ": " + repr(clean)


def test_the_BARREN_counter_reads_each_processes_OWN_stdout():
    """The `barren` counter is a PRODUCER claim, so it is graded at the producer.

    `barren += out.count("BARREN")` could be disabled with the hand-built
    receipts still green (a witness that passes `barren=4` proves nothing about
    the counter) — and the printed receipt would then say `supported: true`
    while the exit code said 2 (#14480 review, cycle 15 P2). This drives the real
    `_claim_race` with the arrival barrier stubbed to expire for every process.

    MUTATION THAT REDS THIS: delete the `barren += out.count("BARREN")` line.
    """
    import tools.measure_drain_convergence as M

    receipt = M._claim_race(3, acquire_setup=(
        "def acquire(root, leg=None):\n"
        "    print('BARREN', flush=True)\n"
        "    raise SystemExit(0)\n"
    ))
    assert receipt["barren"] == 3, receipt
    assert receipt["winners"] == 0, receipt
    # `_claim_race` returns the RACE dict; `_no_verdict` reads it under `claim_race`
    # on a top-level receipt, so wrap it rather than passing the race itself.
    assert M._no_verdict({"claim_race": receipt}) is True, receipt


def test_TWO_winners_is_a_FINDING_not_a_no_verdict():
    """The other side, and the reason the winners check is `== 0` and not `!= 1`:
    TWO winners is the defect this harness exists to measure. Folding it into "no
    verdict" would silence the measurement in exactly the case it was built for.

    ⛔ THE SHAPE THIS BUILDS IS REACHABLE NOW, and that is pinned by its own row:
    `test_the_CLAIM_RACE_BARRIER_reports_TWO_winners_as_a_FINDING` drives the real
    `_claim_race` and gets `bounded: true, winners: 2+` out of it. Before that fix
    this receipt was unreachable from the producer and only this hand-built witness
    said it was a FINDING (#14480 review, F1).

    MUTATION THAT REDS THIS: make the row `winners != 1` — the harness then reports
    NO VERDICT on the finding it was written to find.
    """
    import tools.measure_drain_convergence as M

    assert M._no_verdict(_receipt(supported=False, bounded=True, winners=2,
                                  processes=10)) is False
    assert M._no_verdict({"claim_race": {"supported": False, "winners": None}}) is False


def test_the_measurement_EXITS_NONZERO_on_a_no_verdict_receipt(monkeypatch, capsys):
    """The gate itself, end to end: `main` must return 2 when the measurement it
    produced is a no-verdict, and 0 when it is a real one.

    ⛔ THIS IS THE ROW THE TOOL DID NOT HAVE: `git grep measure_drain_convergence --
    tests/` returned 0 hits, so deleting the `if _no_verdict(receipt): … return 2`
    block redded nothing (#14480 review, F2).

    MUTATION THAT REDS THIS: delete that block — the no-verdict receipt then exits 0.
    """
    import tools.measure_drain_convergence as M

    monkeypatch.setattr(M, "measure", lambda *a, **k: _receipt(
        supported=False, bounded=True, winners=0, processes=10))
    assert M.main(["--scenario", "baseline"]) == 2
    assert "NO VERDICT" in capsys.readouterr().err

    monkeypatch.setattr(M, "measure", lambda *a, **k: _receipt(
        supported=True, bounded=True, winners=1, barren=0, processes=10))
    assert M.main(["--scenario", "baseline"]) == 0


def test_the_measurement_EXITS_ZERO_on_a_real_two_drainer_FINDING(monkeypatch):
    """A two-winner receipt is a FINDING, not a failure of the run: exit 0, with the
    count in the receipt. A gate that refused it would refuse to report the bug.

    ⛔ The receipt is the shape `_claim_race` produces (see
    `test_the_CLAIM_RACE_BARRIER_reports_TWO_winners_as_a_FINDING`); `measure` is
    stubbed only so the exit code can be read without spawning a real race.

    MUTATION THAT REDS THIS: make `_no_verdict` fire on `winners != 1`.
    """
    import tools.measure_drain_convergence as M

    monkeypatch.setattr(M, "measure", lambda *a, **k: _receipt(
        supported=False, bounded=True, winners=2, barren=0, processes=10))
    assert M.main(["--scenario", "baseline"]) == 0


def test_the_CLAIM_RACE_BARRIER_reports_TWO_winners_as_a_FINDING():
    """⛔ THE ROW THE TOOL DID NOT HAVE: a receipt `_claim_race` ACTUALLY PRODUCES
    when more than one process holds the claim.

    The witness above pins `_no_verdict` on a hand-built receipt, and the barrier
    used to make that receipt UNREACHABLE: the hold waited for `processes - 1`
    `done` markers while a winner only wrote its own AFTER releasing, so with `k`
    winners the marker population topped out at `processes - k`. Every winner then
    blocked to the 60 s deadline, `bounded` became False, and the two-drainer
    FINDING the harness exists to report came out as *"NO VERDICT — re-run on a
    quieter box"* (exit 2). Two tests certified a shape the producer could not emit
    (#14480 review, F1).

    So this drives the real `_claim_race` — real subprocesses, real barrier, real
    receipt — with the CLAIM PROTOCOL stubbed to admit every contender. That is the
    defect being measured, reproduced at the seam: the barrier must survive it and
    the receipt must be a FINDING, not a no-verdict.

    MUTATION THAT REDS THIS: restore the hold to `processes - 1` and move the
    winner's marker back after `c.release()` — `bounded` flips to False (after a
    60 s stall) and `_no_verdict` then reports no verdict.
    """
    import tools.measure_drain_convergence as M

    receipt = M._claim_race(3, acquire_setup=(
        "class _AlwaysWins:\n"
        "    def release(self):\n"
        "        pass\n"
        "def acquire(root, leg=None):\n"
        "    return _AlwaysWins()\n"
    ))
    assert receipt["winners"] == 3, receipt
    assert receipt["barren"] == 0, receipt
    assert receipt["bounded"] is True, (
        "the barrier must not wait on winners: every peer had finished attempting, "
        "so nothing straggled — " + repr(receipt))
    assert receipt["supported"] is False, receipt
    assert M._no_verdict({"claim_race": receipt}) is False, (
        "a real two-drainer race is the FINDING this harness was built for, not a "
        "no-verdict — " + repr(receipt))


def test_a_FAILED_restore_does_not_delete_the_only_copy(tmp_path, monkeypatch):
    """⛔ A FAILED RESTORE MUST NOT DESTROY THE CLAIM IT COULD NOT PUT BACK
    (review, cycle 16, P3).

    `_takedown`/the clear already vacated the claim path, so once the object is in the
    quarantine it is the ONLY copy of a live holder's claim. The failure branch used to
    warn and then `unlink` it, emptying the path for the next `link` — two holders for
    up to `TTL/3`, silently, with the moved-aside evidence gone too. `link(2)` lists
    `EACCES`/`EDQUOT`/`EIO`/`EMLINK`/`ENOSPC`/`EPERM`/`EROFS`/`EXDEV` besides
    `EEXIST`, and `ENOSPC`/`EDQUOT` on a spool directory is the ordinary case.

    The answer stays HELD (fail CLOSED: `acquire` → `None` → `held_by_claim`, nothing
    attempted). ⛔ Not `REFUSED`: that raises `DrainClaimUnavailable`, whose handler
    finds an empty path and runs the pass UNCLAIMED — strictly worse.

    MUTATION THAT REDS THIS: restore the unconditional `os.unlink(quarantine)` on the
    failure branch (the object vanishes from disk, and the second assertion fails).
    """
    if not hasattr(os, "mkfifo"):
        pytest.skip("no mkfifo on this platform")
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    fifo = drain_claim.claim_path(root)
    os.mkfifo(fifo)
    peer_raw = json.dumps({
        "version": drain_claim.CLAIM_VERSION, "token": "peer-token", "leg": "pi",
        "pid": os.getpid(), "host": drain_claim._host(), "started_at": 0.0,
        "expires_at": time.time() * drain_claim.TIMESTAMP_UNIT_MS + 600_000,
    })
    real_rename = os.rename
    installed: list = []

    def peer_installs_first(src, dst, *a, **kw):
        if str(src) == str(fifo) and not installed:
            installed.append(src)
            os.unlink(src)
            Path(src).write_text(peer_raw)
        return real_rename(src, dst, *a, **kw)

    real_link = os.link

    def link_fails(src, dst, *a, **kw):
        if ".tmp-clear-" in str(src):
            raise OSError(28, "No space left on device")  # ENOSPC, not EEXIST
        return real_link(src, dst, *a, **kw)

    monkeypatch.setattr(os, "rename", peer_installs_first)
    monkeypatch.setattr(os, "link", link_fails)
    try:
        outcome = drain_claim._clear_unusable_claim_path(fifo)
    finally:
        monkeypatch.undo()
    assert installed, "the test never reached the removal"
    assert outcome == drain_claim._CLEAR_HELD, (
        "a failed restore is still CONTENTION (fail closed), got " + repr(outcome))
    leftovers = list(root.glob("*.tmp-clear-*"))
    assert leftovers, (
        "the only copy of the peer's claim was DELETED when the restore failed")
    assert json.loads(leftovers[0].read_text())["token"] == "peer-token"


def test_an_UNREADABLE_quarantined_claim_whose_RESTORE_FAILS_is_kept(
        tmp_path, monkeypatch):
    """⛔ THE `_restore_quarantine` HALF, WHICH THE ROW ABOVE DOES NOT REACH (review,
    cycle 16, P1).

    The row above drives `_dispose_quarantine`: its peer claim is READABLE, so
    `landed is not None` and the `_lstat_is_regular` branch — the one that calls
    `_restore_quarantine` — never executes. `grep -rn _restore_quarantine tests/`
    returned zero hits, so the function's own failure branch was unpinned, and it
    still unlinked the only copy.

    This row makes the quarantine UNREADABLE (so `landed is None` and the lstat says
    regular), makes `link` fail with `ENOSPC` (a non-`EEXIST` errno — the ordinary
    full-disk case on a spool directory), and requires the peer's bytes to survive.

    MUTATION THAT REDS THIS: restore the unconditional `os.unlink(quarantine)` in the
    `except OSError` branch (the object vanishes and the "still on disk" assertion
    fails).
    """
    if not hasattr(os, "mkfifo"):
        pytest.skip("no mkfifo on this platform")
    root = tmp_path / "spool"
    root.mkdir(parents=True)
    fifo = drain_claim.claim_path(root)
    os.mkfifo(fifo)
    peer_raw = json.dumps({
        "version": drain_claim.CLAIM_VERSION, "token": "peer-token", "leg": "pi",
        "pid": os.getpid(), "host": drain_claim._host(), "started_at": 0.0,
        "expires_at": time.time() * drain_claim.TIMESTAMP_UNIT_MS + 600_000,
    })
    real_rename = os.rename
    installed: list = []

    def peer_installs_first(src, dst, *a, **kw):
        if str(src) == str(fifo) and not installed:
            installed.append(src)
            os.unlink(src)
            Path(src).write_text(peer_raw)
        return real_rename(src, dst, *a, **kw)

    real_read_raw = drain_claim._read_raw
    real_link = os.link

    def read_fails_in_the_quarantine(path):
        # A REGULAR file whose BYTES cannot be had: `landed is None`, and the lstat
        # below still says regular — the `_restore_quarantine` branch.
        if ".tmp-clear-" in str(path):
            return None
        return real_read_raw(path)

    def link_fails(src, dst, *a, **kw):
        if ".tmp-clear-" in str(src):
            raise OSError(28, "No space left on device")  # ENOSPC, not EEXIST
        return real_link(src, dst, *a, **kw)

    monkeypatch.setattr(os, "rename", peer_installs_first)
    monkeypatch.setattr(drain_claim, "_read_raw", read_fails_in_the_quarantine)
    monkeypatch.setattr(os, "link", link_fails)
    try:
        outcome = drain_claim._clear_unusable_claim_path(fifo)
    finally:
        monkeypatch.undo()
    assert installed, "the test never reached the removal"
    assert outcome == drain_claim._CLEAR_HELD, (
        "a failed restore is still CONTENTION (fail closed), got " + repr(outcome))
    leftovers = list(root.glob("*.tmp-clear-*"))
    assert leftovers, (
        "the only copy of the peer's claim was DELETED when the restore failed")
    assert json.loads(leftovers[0].read_text())["token"] == "peer-token"
