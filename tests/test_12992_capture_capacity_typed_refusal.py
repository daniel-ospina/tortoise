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
    and the `detail`-shape assertions below fail (the first on `isinstance`) — the refusal itself
    still asserts clean, so the red is the SHAPE, not the refusal. That is the pre-fix state, exactly.
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
    # ⛔ BOTH keys are pinned — but read this assertion for what it can DO. At a
    # refusal `in_flight` is NECESSARILY equal to `limit`: the gate raises on
    # `_CAPTURE_IN_FLIGHT >= _CAPTURE_MAX_IN_FLIGHT` while holding the lock, so
    # this pair cannot distinguish a live read from a ceiling re-read. What it
    # pins is that the body carries the two keys AT ALL — BOTH keys arrived with
    # this body (#12992); the pre-fix shape was a BARE STRING, not a `limit`-only
    # dict, and #11225 is an OPEN issue that shipped nothing (an earlier cut of
    # this comment said it shipped that shape).
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

    ⛔ AND THE CARVE-OUT IS COUNTER-FREE, which is the half a status+detail
    assertion cannot see (#13000 review, P3 — measured): the counter increment
    added to the CAPACITY arm would, if copied into this one, leave
    `refusing: true` on `/health` for a duplicate-in-flight collision — a
    refusal `_capture_health` does not claim to count ("Refusals the capacity
    gate has ANSWERED") and `docs/infra-runbook.md` §4.1.1b does not document.
    Mutating `_CAPTURE_REFUSED_TOTAL += 1` into this arm kept the whole file
    green before the two asserts below existed.
    """
    monkeypatch.setattr(ha, "_CAPTURE_MAX_IN_FLIGHT", 4)
    monkeypatch.setattr(ha, "_CAPTURE_IN_FLIGHT", 0)
    monkeypatch.setattr(ha, "_CAPTURE_SESSIONS", {})
    # ⛔ SEEDED, not assumed zero: an earlier case in THIS file drives a real
    # capacity refusal, so the module counters are non-zero on entry and a bare
    # `== 0` assertion would fail for ORDER, not for the defect it is pinning
    # (measured: passes alone, reds in the file). Seeding also makes the
    # mutation visible in either order.
    monkeypatch.setattr(ha, "_CAPTURE_REFUSED_TOTAL", 0)
    monkeypatch.setattr(ha, "_CAPTURE_LAST_REFUSED_AT", None)
    slot = ha._reserve_capture_slot("same-session")
    try:
        with pytest.raises(ha.HTTPException) as excinfo:
            ha._reserve_capture_slot("same-session")
    finally:
        slot.release()
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == ha._CAPTURE_SESSION_IN_FLIGHT_DETAIL
    assert ha._CAPTURE_REFUSED_TOTAL == 0, (
        "a 409 duplicate-in-flight collision was COUNTED — `/health` would then "
        "report `refusing: true` for a refusal the capacity gate never issued")
    assert ha._CAPTURE_LAST_REFUSED_AT is None, (
        "a 409 stamped the refusal clock — `refusing` is the CAPACITY gate's "
        "window, and this arm must not move it")


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
    `change_feed` and `wait_bound_breaches` follow (`record`'s `.ok` DOES — see below).

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
    tidy: it is the class `health()` already records in its `refusals` degraded arm
    (the `wait_bound_breaches` arm above it states the same rule) — "an ad-hoc
    `{"error", "refusing"}` dict is 2 of 13 keys, so a dashboard reading
    `refusals["share"]` … gets a `KeyError` on
    exactly the degraded path where it is looking for an answer", measured twice
    (#12992 review P2).

    The degraded arm must answer with the runbook's §4.1.1b key set, not a
    shorter one. ⛔ Where it is REACHABLE, stated honestly: `_capture_health`
    reads `_CAPTURE_EXECUTOR._max_workers` inside its `try`, but a shut-down or
    collected executor still carries `_max_workers` (measured: `2` after
    `shutdown(wait=True, cancel_futures=True)`), and `_CAPTURE_EXECUTOR` is
    bound once and never rebound — so the arm is entered by an UNEXPECTED raise
    in the guarded block, which is what the `_DeadPool` stub below supplies, and
    not by retiring the pool. The key-set contract is what this test pins.
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


def test_the_refusal_reaches_the_wire_as_a_typed_body_and_a_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE WIRE SHAPE, asserted through the APP — not on the `HTTPException`.

    Everything above this test reads the exception object. That is one layer
    short of what a client actually receives: the body a caller parses is
    produced by FastAPI's `http_exception_handler`, and this module already
    departs from that default for `Exception` (its own handler at
    `hosted_api.py`'s `@app.exception_handler(Exception)`), so "the exception
    carries a dict detail" and "the wire answers `{"detail": {...}}`" are two
    claims, not one. The issue's whole premise is what a CLIENT does with the
    refusal — `#12992` records two captures lost because the caller could only
    prose-match — so the claim that a caller can branch on `detail.code` and
    wait `detail.retry_after` has to be pinned where the caller reads it.

    No DB is touched: the admission gate is the FIRST thing the handler does
    after the scope check, so a saturated gate refuses before any state is
    written (that ordering is what `_reserve_capture_slot`'s docstring is
    about), and `legacy_full_access` satisfies `_require_scope` without a
    credential lookup.

    MUTATION THAT REDS THIS: re-type the raise's `detail` as the pre-#12992
    positional string, or drop `headers={"Retry-After": ...}` -> the body is a
    string (`.json()["detail"]["code"]` raises) and/or the header is `None` on
    the response, while every exception-object test above stays green.
    """
    from fastapi.testclient import TestClient

    from tortoise.hosted_api import app, get_current_org_gated

    monkeypatch.setattr(ha, "_CAPTURE_MAX_IN_FLIGHT", 1)
    monkeypatch.setattr(ha, "_CAPTURE_IN_FLIGHT", 0)
    monkeypatch.setattr(ha, "_CAPTURE_SESSIONS", {})
    monkeypatch.setattr(ha, "_CAPTURE_REFUSED_TOTAL", 0)
    monkeypatch.setattr(ha, "_CAPTURE_LAST_REFUSED_AT", None)

    # `legacy_full_access` is the documented scope short-circuit, so the handler
    # reaches admission without a key/session lookup (no DB, no network).
    app.dependency_overrides[get_current_org_gated] = lambda: {
        "org_id": "org-12992-wire", "key_id": None, "legacy_full_access": True,
        "tier": "free", "max_points": 100000, "max_sessions": None}
    held = [ha._reserve_capture_slot("wire-holder")]
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/sessions",
                json={"conversation": [{"role": "user", "content": "hi"}]})
        # Read BEFORE the holder is released: a refusal must not add a slot of its
        # own, so the count is the holder's 1 and no more.
        in_flight_after_refusal = ha._CAPTURE_IN_FLIGHT
        refused_after = ha._CAPTURE_REFUSED_TOTAL
    finally:
        for slot in held:
            slot.release()
        app.dependency_overrides.pop(get_current_org_gated, None)

    assert response.status_code == 429, response.text
    # The header a plain HTTP client honours, on the RESPONSE.
    assert response.headers.get("retry-after") == "30", response.headers
    body = response.json()
    detail = body["detail"]
    assert isinstance(detail, dict), (
        "the wire body's `detail` is not a JSON object — a caller cannot read "
        f"`detail.code` (got {type(detail).__name__}: {detail!r})")
    assert detail["code"] == "capture_capacity_saturated"
    # Body and header must not disagree: one contract, two carriers.
    assert detail["retry_after"] == int(response.headers["retry-after"])
    assert detail["in_flight"] == 1 and detail["limit"] == 1
    # The refusal is counted on the SAME path a client just exercised.
    assert refused_after == 1
    assert in_flight_after_refusal == 1, (
        "the refusal leaked a slot — a refusal is answered BEFORE the admission "
        "increment, so in_flight must be whatever the holder took and no more")
