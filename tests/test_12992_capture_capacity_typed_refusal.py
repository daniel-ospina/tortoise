"""#12992 — a capacity refusal must be TYPED, and the gate's refusals must be VISIBLE.

The defect the issue records: two captures from a live agent session were refused
with `429` and the caller could only log

    [reflect-hook] Hosted capture FAILED (HTTP 429 — "capture capacity saturated —
    too many captures in flight; retry shortly") — a manual-recovery JSONL record
    was kept

so the refusal arrived as (a) a POSITIONAL string plus a header that client never
reads, and (b) a datum that existed nowhere afterwards. The investigation of the
issue could not answer "what saturated the gate" from any surface — no counter
existed (`rg -n 'capture' hosted_api.py | rg -i 'health|metric'` found an
unrelated comment).

Two properties are pinned here, and each is a MUTATION TARGET:

* `_reserve_capture_slot`'s capacity arm answers `detail.code` +
  `detail.retry_after` (the SAME contract the sibling `429 ingest_rate_limited`
  on this route publishes) while KEEPING the `Retry-After` header and the exact
  pre-#12992 message — so a client that branches on the code, a client that reads
  the header, and a legacy client that prose-matches all keep working.
* `_capture_health()` reports capacity AND refusals, and a saturated gate does
  NOT degrade `/health`'s `status`.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import tortoise.hosted_api as ha


def _reserve_with_ceiling(monkeypatch: pytest.MonkeyPatch, ceiling: int):
    """Fill the gate to `ceiling` and return the refusal it raises."""
    monkeypatch.setattr(ha, "_CAPTURE_MAX_IN_FLIGHT", ceiling)
    monkeypatch.setattr(ha, "_CAPTURE_IN_FLIGHT", 0)
    monkeypatch.setattr(ha, "_CAPTURE_SESSIONS", {})
    held = [ha._reserve_capture_slot(f"k{i}") for i in range(ceiling)]
    try:
        with pytest.raises(ha.HTTPException) as excinfo:
            ha._reserve_capture_slot("refused")
    finally:
        for slot in held:
            slot.release()
    return excinfo.value


def test_the_capacity_refusal_names_itself_and_the_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`detail` is a TYPED condition, not the positional string #11225 records.

    MUTATION THAT REDS THIS: restore `detail=("capture capacity saturated — ...")`
    at the raise site -> `detail` is a `str`, `detail["code"]` raises `TypeError`,
    and every assertion below fails. That is the pre-fix state, exactly.
    """
    exc = _reserve_with_ceiling(monkeypatch, 2)

    assert exc.status_code == 429
    # The header is KEPT: an HTTP client that honours `Retry-After` must not
    # lose the instruction to the new body shape.
    assert exc.headers is not None
    assert exc.headers["Retry-After"] == "30"

    detail = exc.detail
    assert isinstance(detail, dict), (
        "the capacity refusal is a bare string again — a client must prose-match "
        "it and cannot read `retry_after` (#12992 / #11225)")
    assert detail["code"] == "capture_capacity_saturated"
    assert detail["retry_after"] == 30
    # The capacity the caller ACTUALLY hit, not a re-read of the ceiling: a
    # refusal that reported the ceiling without the live count cannot be told
    # apart from a burst that has already cleared.
    assert detail["in_flight"] == 2
    assert detail["limit"] == 2
    # The pre-#12992 sentence survives VERBATIM inside the body, so a legacy
    # reader that matches on prose keeps matching (`resp.text` still contains it).
    assert detail["message"] == (
        "capture capacity saturated — too many captures in flight; retry shortly")


def test_the_duplicate_in_flight_refusal_is_UNCHANGED(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 409's detail is a string and must stay one.

    It is a carve-out KEY, compared by identity in two places
    (`hosted_api.py`'s last-error exemption and `mcp_server.py`'s twin), so
    re-typing it alongside the capacity 429 would silently stop both from
    exempting it — a concurrency condition would be painted on the dashboard as
    an org capture failure.
    """
    monkeypatch.setattr(ha, "_CAPTURE_MAX_IN_FLIGHT", 4)
    monkeypatch.setattr(ha, "_CAPTURE_IN_FLIGHT", 0)
    monkeypatch.setattr(ha, "_CAPTURE_SESSIONS", {})
    slot = ha._reserve_capture_slot("same-session")
    try:
        with pytest.raises(ha.HTTPException) as excinfo:
            ha._reserve_capture_slot("same-session")
    finally:
        slot.release()
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == ha._CAPTURE_SESSION_IN_FLIGHT_DETAIL


def test_a_refusal_is_counted_and_surfaces_on_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/health` answers "is the gate refusing NOW" — the datum the brief lacked.

    MUTATION THAT REDS THIS: drop the `_CAPTURE_REFUSED_TOTAL += 1` /
    `_CAPTURE_LAST_REFUSED_AT = ...` pair from the raise site -> the counter does
    not move and the refusal is invisible again, which is defect #3 of the issue.
    """
    monkeypatch.setattr(ha, "_CAPTURE_REFUSED_TOTAL", 0)
    monkeypatch.setattr(ha, "_CAPTURE_LAST_REFUSED_AT", None)

    before = ha._capture_health()
    assert before["refused_total"] == 0
    assert before["refusing"] is False
    assert before["error"] is None

    _reserve_with_ceiling(monkeypatch, 1)

    after = ha._capture_health()
    assert after["refused_total"] == 1
    assert after["refusing"] is True, (
        "a refusal that JUST happened did not mark the window — the read is "
        "answering 'has this ever happened', not 'is this happening'")
    assert after["last_refused_at"] is not None
    # Capacity facts, and the one that is NOT the ceiling: the pool runs FEWER
    # workers than the gate admits (8 vs 4 by default), so a reader who saw only
    # `max_in_flight` would conclude eight extractions can run at once.
    assert after["max_in_flight"] == 1  # the patched ceiling
    assert after["workers"] == ha._CAPTURE_EXECUTOR._max_workers
    assert after["code"] == "capture_capacity_saturated"
    assert after["retry_after"] == 30


def test_an_old_refusal_does_not_read_as_refusing_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`refusing` is WINDOWED. A cumulative total must not read as a live state.

    The forever-red failure #10185 forbids, applied to this block: a burst at
    09:00 must not make `/health` claim the gate is refusing at 17:00 for the life
    of the process.
    """
    monkeypatch.setattr(ha, "_CAPTURE_REFUSED_TOTAL", 7)
    monkeypatch.setattr(
        ha, "_CAPTURE_LAST_REFUSED_AT",
        time.time() - (ha._CAPTURE_REFUSAL_WINDOW_S + 60))

    block = ha._capture_health()

    assert block["refused_total"] == 7  # the history is still reported...
    assert block["refusing"] is False   # ...and is not a claim about NOW
    assert block["last_refused_at"] is not None


def test_a_saturated_gate_does_not_degrade_health_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The block is REPORTED, never folded into `status`.

    A full capture gate refuses `POST /v1/sessions` while every other route is
    healthy, so folding it would restart-loop a process that is serving fine and
    would NOT drain the extractions holding the slots — the same rule
    `change_feed`, `record` and `wait_bound_breaches` follow.

    The three terms `status` IS built from (`db.ok`, `backup_watcher.ok`,
    `record.ok`) are forced healthy here, and the gate is forced FULL: the only
    difference between the two bodies is the capture block, so `status` moving
    would be attributable to it and nothing else. (Asserting `status == "ok"`
    without pinning those terms measures this lane's DB, not the rule.)

    MUTATION THAT REDS THIS: add `or not _capture_health()["refusing"]` to the
    `status` expression -> the second body reports `degraded`.
    """
    monkeypatch.setattr(ha._HEALTH_PROBE, "snapshot",
                        lambda: {"ok": True, "latency_ms": 0.0, "error": None})
    monkeypatch.setattr(ha, "_backup_watcher_health",
                        lambda: {"ok": True, "state": "active"})
    monkeypatch.setattr(ha, "_record_health", lambda: {"ok": True, "armed": False})
    # Order-independent: the earlier tests in this file raise REAL refusals, and
    # these two globals are not in `tests/_verdict.PROCESS_GLOBALS`, so a control
    # that read them unset would depend on which test ran first.
    monkeypatch.setattr(ha, "_CAPTURE_REFUSED_TOTAL", 0)
    monkeypatch.setattr(ha, "_CAPTURE_LAST_REFUSED_AT", None)
    monkeypatch.setattr(ha, "_CAPTURE_IN_FLIGHT", 0)
    monkeypatch.setattr(ha, "_CAPTURE_MAX_IN_FLIGHT", 8)

    healthy = asyncio.run(ha.health())
    assert healthy["status"] == "ok"      # the control: nothing else is red
    assert healthy["capture"]["refusing"] is False

    monkeypatch.setattr(ha, "_CAPTURE_MAX_IN_FLIGHT", 1)
    monkeypatch.setattr(ha, "_CAPTURE_IN_FLIGHT", 1)  # gate full, right now
    monkeypatch.setattr(ha, "_CAPTURE_REFUSED_TOTAL", 3)
    monkeypatch.setattr(ha, "_CAPTURE_LAST_REFUSED_AT", time.time())

    body = asyncio.run(ha.health())

    assert body["capture"]["refusing"] is True
    assert body["capture"]["in_flight"] == 1
    assert body["status"] == "ok", (
        "a saturated capture gate degraded liveness — a busy gate is not a dead "
        "process (#12992)")


def test_the_degraded_arm_publishes_the_same_keys_as_the_healthy_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_capture_health()` has TWO arms and a reader must not get a `KeyError`.

    MUTATION THAT REDS THIS: shrink the `except` arm back to the ad-hoc short
    dict it was written as (7 keys, no `refusal_window_s` / `code` /
    `retry_after`) -> the key-set equality below fails. That is not hypothetical
    tidy: it is the class this same module already records 250 lines away for
    `wait_bound_breaches` — "an ad-hoc `{"error", "refusing"}` dict is 2 of 13
    keys, so a dashboard reading `refusals["share"]` … gets a `KeyError` on
    exactly the degraded path where it is looking for an answer", measured twice
    (#12992 review P2).

    ⚠️ THE ARM IS DEFENSIVE, NOT REACHABLE FROM A SHIPPED PATH — stated plainly
    because an earlier cut of this docstring claimed the opposite and was
    MEASURED false (#12992 review). `_CAPTURE_EXECUTOR` is a module global that
    is never reassigned, and a SHUT-DOWN executor still carries `_max_workers`
    (`ThreadPoolExecutor(2).shutdown()`, then `._max_workers` → still `2`), so
    nothing in production makes that read raise — the module's own comment inside
    the `try` says exactly that ("a shut-down executor still carries it"). The
    test therefore FORCES the arm through a non-executor object, because the rule
    the repair follows is not about reachability: `docs/infra-runbook.md` §4.1.1b
    documents ten keys, and the `wait_bound_breaches` precedent above is that
    EVERY return of a documented block carries the documented key set — a reader
    must never get a `KeyError` on the one path that exists to answer when the
    measurement failed.
    """
    healthy = ha._capture_health()

    class _DeadPool:
        @property
        def _max_workers(self):
            raise RuntimeError("capture pool is gone")

    monkeypatch.setattr(ha, "_CAPTURE_EXECUTOR", _DeadPool())
    degraded = ha._capture_health()

    assert degraded["error"], "the degraded arm must say WHY it could not measure"
    assert set(degraded) == set(healthy), (
        "the degraded arm publishes a different key set than the healthy one — a "
        "dashboard/jq reader gets a KeyError on exactly the path where it is "
        "looking for an answer (#12992 review P2)")
    # The capacity identity is still true on the degraded arm: these are constants,
    # not measurements, so nothing about a dead pool makes them unknown.
    assert degraded["code"] == healthy["code"]
    assert degraded["retry_after"] == healthy["retry_after"]
    assert degraded["refusal_window_s"] == healthy["refusal_window_s"]
