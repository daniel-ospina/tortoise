"""#14342 — the door stops refusing: a burst past the ceiling is ACCEPTED and every
turn is eventually processed.

`tests/test_capture_intake.py` pins the QUEUE's four invariants without a server.
This file pins the two properties the brief's acceptance criteria name, at the
seam between the door and the queue:

* **a burst past the old 16 is accepted, not refused** — every over-ceiling
  capture answers 202 with a status address, and none answers 429;
* **no turn disappears** — N accepted in, N processed out, and a drain that dies
  mid-item leaves the item recoverable rather than invisible.

⛔ The capture work itself is stubbed (`_capture_session_impl`) throughout: this
file is about the DOOR and the DRAIN. What the capture path does with the payload
is #14342's unchanged behaviour and its own suite's business.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import tortoise.hosted_api as ha
from tortoise import capture_intake as ci

#: The REAL capture pipeline, captured at import time — the gate-order test below
#: needs it while the shared fixture stubs the impl for the door/drain tests.
_REAL_IMPL = ha._capture_session_impl


async def _gates_pass_impl(body, request, org, slot=None, state=None,
                           deferred_resume=False, gate_only=False,
                           ingest_charged=False):
    """Stand in for the capture path's GATES: pass, then stop.

    ⛔ The door runs the entry gates BEFORE it converts a capacity refusal into
    an acceptance (#14707 review P1), through
    `_capture_session_impl(gate_only=True)`. This stub answers that preflight
    with the sentinel the real implementation raises at the first-write
    boundary, so these door/drain tests stay graph-free. The gates THEMSELVES are
    exercised against the REAL implementation by
    `test_the_entry_gates_run_before_the_acceptance` below.
    """
    raise ha._CaptureEntryGatesSatisfied()


@pytest.fixture()
def intake(monkeypatch: pytest.MonkeyPatch):
    """Point the door and the drain at ONE in-memory store for this test.

    The production store is one row per tenant in the tenant's record; the seam
    under test is identical (`IntakeStore`), which is why the memory store is a
    legitimate substrate here and NOT a substitute for the graph store's own
    query tests.
    """
    store = ci.MemoryIntakeStore()
    counters = ci.IntakeCounters()

    def _queue_for(_proj_or_org=None):
        return ci.CaptureIntake(store=store, counters=counters)

    monkeypatch.setattr(ha, "_capture_intake_for_proj", _queue_for)
    monkeypatch.setattr(ha, "_CAPTURE_INTAKE_COUNTERS", counters)
    # the accept path opens an SDK and reads the projection; neither is what this
    # test measures, and both need a live graph.
    monkeypatch.setattr(ha, "_capture_intake_accept_sync",
                        lambda org, payload: _queue_for().accept(
                            org_id=org["org_id"], graph=None,
                            session_id=payload["session_id"], payload=payload))
    monkeypatch.setattr(ha, "_capture_intake_claim_sync",
                        lambda org: _queue_for().next_item())
    monkeypatch.setattr(ha, "_capture_intake_reclaim_sync",
                        lambda org: _queue_for().reclaim())
    monkeypatch.setattr(ha, "_capture_intake_finish_sync",
                        lambda org, item: _queue_for().finish(item))
    monkeypatch.setattr(ha, "_CAPTURE_INTAKE_DRAINERS", set())
    monkeypatch.setattr(ha, "_CAPTURE_INTAKE_TASKS", set())
    monkeypatch.setattr(ha, "_CAPTURE_INTAKE_TENANTS", {})
    monkeypatch.setattr(ha, "_CAPTURE_INTAKE_LAST_DRAIN", {})
    # the entry-gate PREFLIGHT the door runs before accepting (see the stub).
    monkeypatch.setattr(ha, "_capture_session_impl", _gates_pass_impl)
    return store


def _body(i: int) -> ha.SessionRequest:
    return ha.SessionRequest(
        session_id=f"s{i}",
        harness="pi",
        conversation=[{"role": "user", "content": f"turn {i}"}],
    )


def _saturated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make admission answer the CAPACITY refusal — the pre-#14342 state."""
    def _refuse(_key=None):
        raise ha.HTTPException(
            status_code=429,
            detail={"code": ha._CAPTURE_CAPACITY_CODE, "message": "saturated",
                    "retry_after": 30, "in_flight": 8, "limit": 8},
            headers={"Retry-After": "30"})

    monkeypatch.setattr(ha, "_reserve_capture_slot", _refuse)


def _org() -> dict:
    return {"org_id": "org_1", "graph_id": None, "legacy_full_access": True,
            "key_id": None}


# ── 1. the door: over the ceiling is a 202, never a 429 ────────────────────

def test_a_saturated_capture_is_accepted_with_a_status_address(
    monkeypatch: pytest.MonkeyPatch, intake,
) -> None:
    _saturated(monkeypatch)
    resp = asyncio.run(ha.capture_session(_body(1), None, _org()))
    assert isinstance(resp, ha.JSONResponse)
    assert resp.status_code == 202, (
        "a saturated capture must be ACCEPTED (202), not refused — the refusal "
        "is what parked 17 894 turns in ~/.tortoise/session-events/")
    payload = json.loads(resp.body)
    assert payload["status"] == "accepted"
    assert payload["extraction"]["phase"] == "queued"
    assert payload["extraction"]["outcome_known"] is False
    # ⛔ #14707 review P3: the client's "outcome never recorded" net reads the
    # TOP-LEVEL marker (`capture_spool.UNRECORDED_OUTCOME_KEY`), so a nested-only
    # `outcome_known` left a queued turn without the client-side net.
    assert payload["extraction_outcome"] == "unknown", payload
    assert payload["status_url"] == "/v1/sessions/s1"
    assert payload["intake"]["item_id"]
    assert payload["intake"]["session_id"] == "s1"


def test_a_minted_session_id_is_stored_so_the_status_address_resolves(
    monkeypatch: pytest.MonkeyPatch, intake,
) -> None:
    """A body with no `session_id` gets one minted at the door — and the ROW must
    carry that same id, or the 202's `status_url` addresses a session that will
    never exist."""
    _saturated(monkeypatch)
    body = ha.SessionRequest(harness="pi",
                             conversation=[{"role": "user", "content": "x"}])
    resp = asyncio.run(ha.capture_session(body, None, _org()))
    payload = json.loads(resp.body)
    sid = payload["session_id"]
    assert sid and payload["status_url"] == f"/v1/sessions/{sid}"
    assert intake.rows()[0].session_id == sid
    assert intake.rows()[0].payload["session_id"] == sid


def test_a_non_capacity_refusal_keeps_its_meaning(
    monkeypatch: pytest.MonkeyPatch, intake,
) -> None:
    """⛔ Every other refusal keeps its meaning (#14707 review P2).

    The PREVIOUS revision of this test stubbed `_reserve_capture_slot` to raise
    `ingest_rate_limited` — an exception that function cannot raise — so it was
    green while the property in its docstring was false in production. The real
    per-org ingest 429 comes from the entry gates, which the door now runs before
    it accepts; this test drives it from there.
    """
    _saturated(monkeypatch)

    async def _rate_limited(body, request, org, slot=None, state=None,
                           deferred_resume=False, gate_only=False,
                           ingest_charged=False):
        raise ha.HTTPException(
            status_code=429,
            detail={"code": "ingest_rate_limited", "retry_after": 30})

    monkeypatch.setattr(ha, "_capture_session_impl", _rate_limited)
    with pytest.raises(ha.HTTPException) as excinfo:
        asyncio.run(ha.capture_session(_body(9), None, _org()))
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail["code"] == "ingest_rate_limited"
    assert intake.rows() == [], "a non-capacity refusal must not queue anything"


def test_the_entry_gates_run_before_the_acceptance(
    monkeypatch: pytest.MonkeyPatch, intake,
) -> None:
    """⛔ THE GATE ORDER, against the REAL capture implementation.

    Pre-fix, `_reserve_capture_slot` was the FIRST thing the handler did, so a
    saturated box answered `202 accepted` to an org whose recording is switched
    OFF: the client pruned its spool entry on the 2xx, the drain re-discovered
    the 409, and a permanently refused turn was lost. Post-fix the capacity
    refusal only ROUTES to the gate preflight: the gates answer 409 first and
    nothing is queued.

    Non-vacuity: with the pre-fix ordering this test gets 202 (asserted below as
    "must not"), not 409 — it is the reviewer's exact scenario, at the one gate
    that needs no graph to reach (`_session_recording_allowed_off_loop`).
    """
    _saturated(monkeypatch)

    async def _recording_off(_org):
        return (False, "team", {})

    monkeypatch.setattr(ha, "_session_recording_allowed_off_loop",
                        _recording_off)
    monkeypatch.setattr(ha, "_capture_session_impl", _REAL_IMPL)
    with pytest.raises(ha.HTTPException) as excinfo:
        asyncio.run(ha.capture_session(_body(11), None, _org()))
    assert excinfo.value.status_code == 409, (
        "a saturated box must not turn the recording-off 409 into a 202 — the "
        "client prunes its spool entry on any 2xx")
    assert intake.rows() == [], (
        "a turn refused by an ENTRY gate must not be queued either")


def test_the_rollback_switch_restores_the_refusal(
    monkeypatch: pytest.MonkeyPatch, intake,
) -> None:
    """`TORTOISE_CAPTURE_INTAKE=0` is the deploy-time rollback, and it must give
    back the exact pre-#14342 behaviour (a 429) rather than a partial one."""
    _saturated(monkeypatch)
    monkeypatch.setenv("TORTOISE_CAPTURE_INTAKE", "0")
    with pytest.raises(ha.HTTPException) as excinfo:
        asyncio.run(ha.capture_session(_body(2), None, _org()))
    assert excinfo.value.status_code == 429
    assert intake.rows() == []


def test_a_store_that_cannot_write_answers_a_retryable_503(
    monkeypatch: pytest.MonkeyPatch, intake,
) -> None:
    """⛔ THE ONLY REFUSAL THIS DESIGN MAY PRODUCE, and it must be retryable.

    The client prunes its spool entry on any 2xx, so acknowledging a turn we
    could not store destroys it (#10479). 503 keeps the turn where it still is.
    """
    _saturated(monkeypatch)

    def _broken(_org, _payload):
        raise ci.IntakeStoreUnavailable("graph down")

    monkeypatch.setattr(ha, "_capture_intake_accept_sync", _broken)
    with pytest.raises(ha.HTTPException) as excinfo:
        asyncio.run(ha.capture_session(_body(3), None, _org()))
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["code"] == "capture_intake_unavailable"
    assert "Retry-After" in excinfo.value.headers


# ── 2. the drain: N in, N out, and a kill loses nothing ────────────────────

def test_a_burst_far_past_the_ceiling_is_accepted_and_all_of_it_is_processed(
    monkeypatch: pytest.MonkeyPatch, intake,
) -> None:
    """THE ACCEPTANCE TEST: 24 captures against a saturated box.

    24 is chosen to be past the hard clamp (16) as well as the default (8), so
    this cannot pass by the ceiling happening to be high. Every one must be
    accepted (202), and every one must reach the capture path exactly once.
    """
    _saturated(monkeypatch)
    n = 24
    codes = []
    for i in range(n):
        resp = asyncio.run(ha.capture_session(_body(i), None, _org()))
        codes.append(resp.status_code)
    assert codes == [202] * n, "no capture in the burst may be refused"
    assert len(intake.rows(state="queued")) == n, "every accepted turn is queued"

    processed: list[str] = []

    async def _fake_impl(body, request, org, slot=None, state=None,
                         deferred_resume=False, gate_only=False,
                         ingest_charged=False):
        processed.append(body.session_id)
        assert deferred_resume is True, (
            "a drained item must take the resume lane, or a recorded-then-"
            "abandoned capture replays with 0 extracted (#1727)")
        assert ingest_charged is True, (
            "the door already charged this org's ingest window — charging it "
            "again would spend it twice for one turn (#14707 review P1)")
        return {}

    monkeypatch.setattr(ha, "_capture_session_impl", _fake_impl)
    keys: list[str | None] = []
    monkeypatch.setattr(ha, "_reserve_capture_slot",
                        lambda _k=None: (keys.append(_k), _Slot())[1])

    asyncio.run(ha._capture_intake_drain(_org(), None, "org_1:None"))

    assert sorted(processed) == sorted(f"s{i}" for i in range(n)), (
        "N in, N out — a turn accepted by the door must reach the capture path")
    assert intake.rows() == [], "a completed item must not linger in the queue"
    assert ha._CAPTURE_INTAKE_COUNTERS.completed == n
    # ⛔ #14707 review P2: the drain reserves with the SESSION KEY, so
    # `_CAPTURE_SESSIONS` (the #3129 duplicate-session admission guard) is armed
    # for drained work — `None` left it open to a concurrent same-session POST.
    assert keys and all(k and k.endswith(tuple(f":s{i}" for i in range(n)))
                        for k in keys), (
        "the drain must reserve with `_capture_session_key(org, session_id)`, "
        f"not None (got {keys[:3]})")


def test_a_drain_killed_mid_item_leaves_it_recoverable_and_it_is_processed(
    monkeypatch: pytest.MonkeyPatch, intake,
) -> None:
    """⛔ THE MID-FLIGHT-KILL TEST.

    The first drain claims an item and is CANCELLED inside the capture path (a
    deploy, a cap, a SIGTERM). The row must not become invisible: a later drain
    reclaims the lease and processes it, so the accepted turn still lands.
    """
    _saturated(monkeypatch)
    asyncio.run(ha.capture_session(_body(1), None, _org()))
    asyncio.run(ha.capture_session(_body(2), None, _org()))

    seen: list[str] = []

    async def _killed_impl(body, request, org, slot=None, state=None,
                           deferred_resume=False, gate_only=False,
                           ingest_charged=False):
        seen.append(body.session_id)
        raise asyncio.CancelledError()

    monkeypatch.setattr(ha, "_capture_session_impl", _killed_impl)
    monkeypatch.setattr(ha, "_reserve_capture_slot", lambda _k=None: _Slot())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ha._capture_intake_drain(_org(), None, "org_1:None"))

    claimed = intake.rows(state="extracting")
    assert len(claimed) == 1, "the killed item is still claimed, not deleted"

    # The lease expires; the next drain reclaims and finishes the work.
    async def _fine_impl(body, request, org, slot=None, state=None,
                         deferred_resume=False, gate_only=False,
                         ingest_charged=False):
        seen.append(body.session_id)
        return {}

    monkeypatch.setattr(ha, "_capture_session_impl", _fine_impl)
    q = ci.CaptureIntake(store=intake, now=lambda: 10 ** 12,
                         counters=ha._CAPTURE_INTAKE_COUNTERS)
    assert q.reclaim() == 1
    asyncio.run(ha._capture_intake_drain(_org(), None, "org_1:None"))

    assert intake.rows() == [], "both turns must have been processed"
    assert len(seen) == 3, (
        "the killed attempt, the reclaimed attempt, and the other item — "
        "nothing else")
    assert sorted(set(seen)) == ["s1", "s2"]


class _Slot:
    """The minimum slot surface the drain touches."""

    def release(self) -> None:
        return None

    def hand_off(self) -> None:
        return None

    def tail_pending(self) -> None:
        return None

    def complete_tail(self) -> None:
        return None

    def worker_done(self) -> None:
        return None

    def hold_until(self, _fut: object) -> None:
        return None


# ── 3. a CONDITION is not a VERDICT (#14707 review P1/P2) ─────────────────

def test_the_drain_separates_transient_conditions_from_permanent_verdicts():
    """⛔ Capacity / in-flight / our own 503 pass; 402/409/400 never do.

    Pre-fix every drain failure spent one of `CAPTURE_INTAKE_MAX_ATTEMPTS`, so a
    saturated box (transient) parked accepted turns `failed` — a lost turn — and
    a permanent refusal burned four minutes re-learning an answer that cannot
    change.
    """
    transient = [
        ha.HTTPException(status_code=429, detail={"code": ha._CAPTURE_CAPACITY_CODE}),
        ha.HTTPException(status_code=429, detail={"code": "ingest_rate_limited"}),
        ha.HTTPException(status_code=503,
                         detail={"code": "capture_intake_unavailable"}),
        ha.HTTPException(status_code=409,
                         detail=ha._CAPTURE_SESSION_IN_FLIGHT_DETAIL),
        # unclassifiable (no HTTP verdict) is retried, never parked on a guess
        ci.IntakeStoreUnavailable("graph down"),
    ]
    for exc in transient:
        assert ha._capture_intake_fault_is_transient(exc) is True, exc

    permanent = [
        ha.HTTPException(status_code=402, detail={"code": "quota_exceeded"}),
        ha.HTTPException(status_code=409, detail="Session recording is disabled"),
        ha.HTTPException(status_code=400, detail="turn cap"),
        ha.HTTPException(status_code=422, detail="blank transcript"),
    ]
    for exc in permanent:
        assert ha._capture_intake_fault_is_transient(exc) is False, exc
