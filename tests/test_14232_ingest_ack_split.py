"""#14232 — the capture ACKs inside the transport bound and extracts behind it.

THE DEFECT THIS PINS
--------------------
`POST /v1/sessions` ran the WHOLE capture inside the request, and the response sat
on the far side of the LLM extraction. The transport wait bound (owner decision
`#3834`, and it must NOT be raised) therefore answered **504** for a capture whose
record was already durable — measured production order: 402 (the `#11892` cap,
lifted) → 401 (no key in env) → 429 (`in_flight 8/8`, raised to 16) → 504 at the
bound with `stage=unmarked spent_ms={}`. Extractor v4 has never completed a live
session, and the path could not even say what was slow.

WHAT IS PINNED HERE, ONE TEST PER RISK IN THE PLAN'S SCOPE CONTRACT
------------------------------------------------------------------
R1  the record (Session + turn Points + the session `:Source`) is durable when the
    ACK is served — §2.0's re-derivability claim, and the reason the split sits
    after the write rather than before it;
R2  the capture reaches a TERMINAL state on its own, and a completion that dies
    mid-flight writes the abandoned marker (never a silent "filed");
R3  a client reading the ACK cannot conclude "nothing was extracted": the body's
    VALUES say in-flight (protocol `status=accepted`, `outcome_known=False`, the
    phase, the count labelled as so-far) and it carries the marker the CLI and
    the spool drain already read as "outcome not recorded yet" — which is what
    keeps a PRICED re-extraction deferral from being written for work that is
    still running (`#12362`);
R3b the in-budget path still returns today's full receipt, and the choice between
    the two bodies is made by cause (completion in the window), never by a
    wall-clock race in the assertion;
R4  the session's in-flight key outlives the tail, so a same-session retry during
    the tail is refused (`409`) instead of replaying over it (`#3129`);
R5  the persisted phase moves `queued` → `extracting` → cleared, and a 504 on this
    path names its stage instead of `unmarked`;
R6  capacity always comes back — including when the completion dies before the
    extraction is even submitted (the direction whose leak is silent: every later
    capture would 429).

Every assertion drives the REAL handler through the real ASGI app and the real
middleware stack. The extractor is the offline `MockModel` seam
(`TORTOISE_SESSION_LLM_MOCK`, installed for the whole suite by
`tests/test_hosted_api.py`'s autouse fixture) except where a test deliberately
PARKs it — parking is an `threading.Event`, and the ack window is forced to zero
by patching `_capture_ack_grace_s`, so no test asserts anything through a sleep.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import threading
import time
from datetime import UTC, datetime

import pytest

import tortoise.hosted_api as ha
from tests.test_hosted_api import TEST_ORG_ID  # fixture side effects
from tests.test_hosted_api import client as client  # the `client` fixture


@pytest.fixture(autouse=True)
def _offline_extractor(monkeypatch):
    """The same offline seam `test_hosted_api.py`'s own autouse fixture installs.

    That fixture is module-scoped to `tests/test_hosted_api.py`, so a test module
    that imports the `client` fixture from it does NOT inherit it — the extraction
    then runs for real and 503s on a keyless box, which on the deferred lane is
    INVISIBLE (the ACK does not report it). Installed here explicitly for that
    reason: a suite that forgets it looks green on the ACK path and proves the
    tail never ran.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


_CONV = [
    {"role": "user",
     "content": "The ingest worker must ack before it extracts anything."},
    {"role": "assistant",
     "content": "Agreed — the receipt says accepted and the status says extracting."},
]


# ── helpers ───────────────────────────────────────────────────────────────

#: The window the DEPLOYED handler uses, READ FROM THE MODULE at import time.
#: It has to be read here rather than in the row: the suite's autouse fixture opens
#: `ha._CAPTURE_ACK_DEADLINE_S` to `_CAPTURE_ACK_WINDOW_S` for the whole session (so
#: no pre-existing assertion races a deadline), so by the time a test body runs the
#: module attribute is no longer the deployed value. Reading it at import — before
#: any fixture runs — is what makes the row's claim ("a value too tight would keep
#: every suite green") about the REAL constant: a literal here would keep passing while
#: `hosted_api._CAPTURE_ACK_DEADLINE_S` was changed to anything at all (measured by
#: review round 5: `0.5 → 0.05` in `hosted_api.py` left this module green).
_PRODUCTION_ACK_DEADLINE_S = ha._CAPTURE_ACK_DEADLINE_S

#: The REAL ceiling function, captured at import for the same reason: the suite's
#: autouse fixture replaces it (so the clamp cannot hold the opened window down to
#: 5 s), and the row that asserts the CLAMP therefore has to put the real one back
#: before it measures the half-bound ceiling.
_REAL_ACK_CEILING = ha._capture_ack_ceiling_s


def _force_ack(monkeypatch):
    """Force the ACK path: the join window is zero, so completion cannot arrive.

    ⛔ Patching the WINDOW rather than the extractor keeps the assertion about
    the handler's own decision. A slow fake extractor would make the test a race
    between two wall clocks; a zero window makes "did the caller get the ack"
    a fact about the code path under test.
    """
    monkeypatch.setattr(ha, "_capture_ack_grace_s", lambda _request: 0.0)


def _force_join(monkeypatch, seconds: float):
    """Force the JOIN path at a REAL deadline — the symmetric twin of `_force_ack`.

    `_force_ack` takes the window to zero so the join can never land; this one
    leaves `_capture_ack_grace_s` REAL and pins the deadline to a chosen value,
    so the join must land for a local mock capture. It exists because the suite's
    autouse fixture opens `_CAPTURE_ACK_DEADLINE_S` to `_CAPTURE_ACK_WINDOW_S` (see its docstring:
    no pre-existing assertion may race a deadline, `#5049` rule 1) — and that
    fixture, left alone, means the PRODUCTION 0.5 s is exercised by no row at
    all, so a value too tight for the joined receipt would keep every suite
    green.

    The transport ceiling is pinned to its production value too
    (`_TRANSPORT_WAIT_BOUND_S` = 10 s), so the value this helper pins is the one
    the join uses: the suite fixture opens the CLAMP along with the window
    (`_capture_ack_ceiling_s` → the live `_CAPTURE_ACK_DEADLINE_S`), so the clamp
    here is `seconds` rather than `min(seconds, 5.0)`. The clamp's own
    production shape is asserted by
    `test_the_ack_window_is_anchored_on_arrival`, which restores the real ceiling.
    """
    monkeypatch.setattr(ha, "_CAPTURE_ACK_DEADLINE_S", seconds)
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 10.0)


def _drain_tails(timeout: float = 30.0) -> None:
    """Wait for every scheduled capture completion to finish."""
    deadline = time.monotonic() + timeout
    while ha._CAPTURE_TAIL_TASKS and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not ha._CAPTURE_TAIL_TASKS, (
        f"capture completions did not finish: {ha._CAPTURE_TAIL_TASKS}")


def _await_phase_gone(session_id: str, deadline_s: float = 20.0) -> list:
    """Poll until the in-flight phase pair is gone (the retire is an OFF-LOOP write).

    ⛔ `_drain_tails()` is NOT enough to observe it, and a review round measured the
    cost of assuming it was: the marker/phase writes are submitted to
    `_CAPTURE_MARKER_EXECUTOR` from the tail task's done-callback, and
    `_CAPTURE_TAIL_TASKS` drops the task BEFORE that submit — so a single read after
    the drain races a thread-pool write. A 50 ms delay inside `_clear_capture_phase`
    (a slow graph write, not a wrong handler) turned both rows RED with the handler
    CORRECT: a host event manufacturing a product verdict, the `#5049` rule-1 class
    this module cites elsewhere. Every pre-existing row that observes this retire
    polls for it; these two now do the same.
    """
    deadline = time.monotonic() + deadline_s
    row = _session_row(session_id)
    while (time.monotonic() < deadline and row
           and (row[0][3] is not None or row[0][4] is not None)):
        time.sleep(0.02)
        row = _session_row(session_id)
    return row


def _proj():
    return ha._make_sdk(namespace=TEST_ORG_ID)._get_proj()


def _session_row(session_id: str) -> list:
    return _proj().g.query(
        "MATCH (s:Session {id:$sid}) "
        "RETURN s.id, s.capture_ok, s.capture_extractor, "
        "       s.capture_extraction_phase, s.capture_extraction_phase_at",
        params={"sid": session_id}).result_set


def _key_held(session_id: str) -> bool:
    """Is this session's in-flight key held? (the registry key is namespaced)"""
    return any(k.endswith(":" + session_id) for k in ha._CAPTURE_SESSIONS)


def _non_turn_points(session_id: str) -> int:
    """Extracted (non-episodic) points CONTAINS-wired to the session."""
    rows = _proj().g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE p.pointKind IS NULL OR p.pointKind <> 'event' "
        "RETURN count(p)", params={"sid": session_id}).result_set
    return int(rows[0][0]) if rows else 0


class _ParkedExtractor:
    """Park the v2 extractor on an Event so the tail cannot finish.

    Patches the SDK METHOD the handler calls (`sdk._extract_session_v2`), which is
    the same seam `sdk.capture_session` uses — so the test parks the real call
    site rather than a helper of the handler's.
    """

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self._orig = None

    def __enter__(self):
        from tortoise.sdk import TortoiseSDK
        self._orig = TortoiseSDK._extract_session_v2
        orig = self._orig

        def _parked(sdk_self, *args, **kwargs):
            self.started.set()
            assert self.release.wait(30.0), "test never released the extractor"
            return orig(sdk_self, *args, **kwargs)

        TortoiseSDK._extract_session_v2 = _parked
        return self

    def __exit__(self, *exc):
        from tortoise.sdk import TortoiseSDK
        TortoiseSDK._extract_session_v2 = self._orig
        self.release.set()
        return False


# ── the window itself (review P2: it had no direct test) ──────────────────

def test_the_ack_window_is_anchored_on_arrival(monkeypatch):
    """`_capture_ack_grace_s` spends the deadline from ARRIVAL, not from the join.

    MEASURED REASON, not a preference: with the window measured from the join's
    start the ack's latency is `record phase + window` and the acceptance drain
    read **p95 1.95 s** (then 1.14 s at a 1 s window) against this issue's 1 s
    target. Every assertion below is a number, because the defect was a unit
    mismatch between two clocks and prose could not have caught it.
    """
    import types

    # ⛔ The suite fixture opens the clamp (`_capture_ack_ceiling_s`) so no other
    # row races a deadline; THIS row is the one that asserts the clamp, so it puts
    # the real function back before measuring the half-bound ceiling.
    monkeypatch.setattr(ha, "_capture_ack_ceiling_s", _REAL_ACK_CEILING)
    monkeypatch.setattr(ha, "_CAPTURE_ACK_DEADLINE_S", 0.5)
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 10.0)

    def _req(age_s):
        state = types.SimpleNamespace(_wait_bound_t0=time.monotonic() - age_s)
        return types.SimpleNamespace(state=state)

    # ⌛ A fresh request gets (almost) the whole window …
    assert 0.4 < ha._capture_ack_grace_s(_req(0.0)) <= 0.5
    # ⌛ … a request whose record phase already spent 0.4 s of it gets the rest,
    #    NOT a fresh 0.5 s — this is the double-payment the anchor removes.
    assert 0.05 < ha._capture_ack_grace_s(_req(0.4)) < 0.15
    # ⌛ … and a request that has spent the deadline gets ZERO, never a negative
    #    wait (a negative `wait_for` raises instead of answering).
    assert ha._capture_ack_grace_s(_req(2.0)) == 0.0
    # ⛔ The CEILING is half the transport bound, so a future bump of the constant
    # can never put the response near `#3834`'s refusal.
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.2)
    assert ha._capture_ack_grace_s(_req(0.0)) <= 0.1 + 0.02
    # An absent binding (a direct call, a unit test) gets the full constant: the
    # window is what makes the join worth doing, and a missing middleware must not
    # silently disable the compatibility half.
    assert ha._capture_ack_grace_s(types.SimpleNamespace(state=None)) == 0.5
    assert ha._capture_ack_grace_s(None) == 0.5


# ── R1: durable at the ACK ────────────────────────────────────────────────

def test_the_record_is_durable_when_the_ack_is_served(client, monkeypatch):
    """§2.0 — the record is truth. The ACK must never precede the write.

    The split sits after the `:Session` MERGE + turn `:Point`s + Event mint +
    `:Source` materialization precisely so a crash between the ACK and completion
    cannot lose a capture the client was told had landed. Moving the split above
    `sdk._materialize_session_source` reds this test on the Source alone (the
    Session and the turn Points survive there, so they cannot discriminate).
    """
    _force_ack(monkeypatch)
    session_id = "s-14232-durable"

    with _ParkedExtractor() as parked:
        r = client.post("/v1/sessions", json={
            "conversation": _CONV, "session_id": session_id})
        assert r.status_code == 200, r.text
        # ⛔ The ACK is already in hand and the extraction is PARKED: everything
        # asserted below is therefore a fact about the state at ACK time.
        assert parked.started.wait(30.0), "the extraction never started"

        row = _session_row(session_id)
        assert row, "the :Session must exist when the ACK is served"
        turn_points = _proj().g.query(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
            "WHERE p.pointKind = 'event' RETURN count(p)",
            params={"sid": session_id}).result_set
        assert int(turn_points[0][0]) == len(_CONV), (
            "the turn Points must exist when the ACK is served")
        source = _proj().g.query(
            "MATCH (src:Source {url:$url}) RETURN src.url",
            params={"url": f"session:{session_id}"}).result_set
        assert source, (
            "the session :Source must exist when the ACK is served — the split "
            "point sits after `_materialize_session_source` so that #8501's "
            "contentHash anchor is in place before any extracted Point records a "
            "sourceVersion")

        parked.release.set()
    _drain_tails()


# ── R2: terminal state on its own ─────────────────────────────────────────

def test_the_capture_reaches_a_terminal_state_behind_the_ack(client, monkeypatch):
    """A `queued`/`extracting` session must not stay that way.

    "Terminal" is the record the whole read path derives from: `capture_ok`
    written and the phase CLEARED in the same statement, so no reader ever has to
    choose between a terminal outcome and a live phase.
    """
    _force_ack(monkeypatch)
    session_id = "s-14232-terminal"

    r = client.post("/v1/sessions", json={
        "conversation": _CONV, "session_id": session_id})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted", r.json()

    _drain_tails()

    row = _session_row(session_id)
    assert row and row[0][1] is True, f"capture_ok must be recorded: {row}"
    assert row[0][2] == "v2", row
    assert row[0][3] is None and row[0][4] is None, (
        f"the terminal record must CLEAR the phase, got {row}")
    # …and extraction demonstrably ran behind the ACK.
    assert _non_turn_points(session_id) >= 1, (
        "the tail must have extracted memory points after the ACK was served")


def test_a_cancelled_completion_writes_the_abandoned_marker(
        client, monkeypatch):
    """R2's other arm: a tail killed mid-flight must not read as fine.

    A cancelled tail leaves `capture_ok = NULL`, which the replay rule reads as
    "presumed captured" — so the next same-session POST would replay a 200 with 0
    extracted, forever (`#3129`). The marker routes that retry into the `#2335`
    TRUE-retry lane instead. Driven by calling the done-callback with a cancelled
    task, because a real cancellation needs a loop shutdown this suite cannot
    perform.
    """
    _force_ack(monkeypatch)
    session_id = "s-14232-cancelled"

    with _ParkedExtractor() as parked:
        r = client.post("/v1/sessions", json={
            "conversation": _CONV, "session_id": session_id})
        assert r.status_code == 200, r.text
        assert parked.started.wait(30.0)

        class _Cancelled:
            def cancelled(self) -> bool:
                return True

        state = {"attempted": True, "finalized": False, "lane": "v2"}
        # ⛔ THE CANCELLED ARM HAS ONE WRITER, and this records who it is
        # (review, round 2 — measured: deleting the `not cancelled` guard from
        # `_capture_tail_failed`'s retire arm left EVERY row green while a
        # cancellation submitted `_clear_capture_phase` beside the marker, two
        # graph writes where the two neighbouring docstrings say one). A pin on the
        # SUBMISSIONS, not on the row: the row looks identical either way — the
        # marker's own statement clears the phase pair, so only the submit
        # distinguishes the contract from the accident.
        cleared: list = []
        _real_clear = ha._clear_capture_phase

        def _recording_clear(proj, sid, *_a, **_k):
            cleared.append(sid)
            return _real_clear(proj, sid, *_a, **_k)

        monkeypatch.setattr(ha, "_clear_capture_phase", _recording_clear)
        ha._capture_tail_failed(_Cancelled(), _proj(), session_id, state)
        # ⛔ ASSERT WHILE THE REAL EXTRACTION IS STILL PARKED. Releasing first
        # would let the genuine completion write `capture_ok=true` over the
        # marker and the test would then be measuring the wrong writer — the
        # marker's claim is about the state a cancelled tail LEAVES BEHIND, not
        # about outrunning a live one.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            row = _session_row(session_id)
            if row and row[0][1] is False:
                break
            time.sleep(0.02)
        assert row and row[0][1] is False, (
            f"a cancelled completion must record capture_ok=false, got {row}")
        assert row[0][2] == "v2", row
        # ⛔ THE MARKER IS A TERMINAL WRITE: it must clear the in-flight phase in
        # the SAME statement. The read path publishes a phase whenever the record
        # carries one, so a marker that left one behind made an abandoned session
        # claim "queued" forever — measured `[[False, 'v2', 'queued']]` — for work
        # nothing was doing, and the three-key pin below exists for every terminal
        # row (`#3520`). Asserted on the SERVED block, not the row: a client reads
        # the block.
        assert row[0][3] is None and row[0][4] is None, (
            f"the abandonment marker must clear the phase pair, got {row}")
        block = client.get(f"/v1/sessions/{session_id}").json()["extraction"]
        assert set(block) == {"status", "extractor", "outcome_known"}, (
            f"a terminal row publishes no phase: {block}")
        assert block["outcome_known"] is True, block
        assert cleared == [], (
            "a CANCELLED tail must not submit the phase clear: the abandoned "
            "marker is the terminal writer on that arm and clears the pair in its "
            f"own statement (submitted: {cleared})")
        parked.release.set()
    _drain_tails()


def test_a_raised_completion_does_NOT_write_the_marker(client, monkeypatch):
    """The other direction, and it is a documented contract: failure ≠ cancellation.

    `_capture_abandoned_marker`'s own docstring scopes it to CANCELLATION — a
    capture that RAISES keeps its NULL→legacy-replay shape
    (`test_hosted_api.py::TestSessionActorStamp2600`'s raise-shape (ii) pins that
    a re-POST of a raise-shaped session must not re-extract). Widening the marker
    to failures would turn every failed extraction into a retry-eligible record.

    ⛔ THE SESSION MUST EXIST FOR THIS ROW TO DISCRIMINATE (review round 4). An
    earlier version of this test passed a session id that was never created and
    asserted `not _session_row(...)`; `_capture_abandoned_marker` is a
    `MATCH … SET`, which cannot create a row, so the assertion was true under the
    very mutant it named (`if (cancelled and …` → `if (state is not None …`).
    An assertion whose value cannot vary under its mutant reads as coverage it
    does not have (`agent-infra#3442` rule 3). The row below writes a REAL
    `:Session` carrying a live phase first, so the widened guard would be caught
    here writing `capture_ok=false`.
    """
    session_id = "s-14232-raised"
    # A real row, with the in-flight phase set as the deferred lane sets it.
    _proj().g.query(
        "MERGE (s:Session {id:$sid}) "
        "SET s.capture_extraction_phase='extracting', "
        "    s.capture_extraction_phase_at='2026-11-21T00:00:00Z'",
        params={"sid": session_id})
    assert _session_row(session_id), "precondition: the session row must exist"

    class _Failed:
        def cancelled(self) -> bool:
            return False

        def exception(self):
            return RuntimeError("boom")

    state = {"attempted": True, "finalized": False, "lane": "v2"}
    ha._capture_tail_failed(_Failed(), _proj(), session_id, state)
    # Both writes the raise arm makes are submitted off the loop, so wait for
    # the observable rather than for a sleep.
    deadline = time.monotonic() + 15
    row = _session_row(session_id)
    while time.monotonic() < deadline and not (row and row[0][3] is None):
        time.sleep(0.02)
        row = _session_row(session_id)
    assert row[0][1] is None, (
        "a raised tail must leave capture_ok NULL — writing it would turn every "
        f"failed extraction into a #2335 TRUE-retry, got {row[0][1]!r}")
    assert row[0][3] is None, (
        f"…and must retire the in-flight phase, got {row[0][3]!r}")


def test_a_raised_completion_retires_the_phase_but_leaves_the_outcome_unrecorded(
        client, monkeypatch):
    """F2's RAISE arm: no outcome recorded — and nothing still claiming to run.

    The marker's scope is CANCELLATION, so a raised tail deliberately writes no
    `capture_ok` (the NULL→legacy-replay contract pinned by the row above). But
    the in-flight phase is a claim that WORK IS RUNNING, and a raised tail is as
    over as a cancelled one: if the phase survives, every read surface publishes
    `phase: "extracting"` with `outcome_known: false` for a capture nothing is
    doing, forever — and the client the ACK told to poll `status_url` waits on a
    corpse. Comparable async-job APIs retire an in-progress state on every exit
    (success, failure, cancellation) rather than from a success-only writer.

    ⛔ The two halves are asserted TOGETHER on purpose: clearing the phase must
    NOT become a second outcome writer. A fix that set `capture_ok=false` here
    would turn every failed extraction into a `#2335` TRUE-retry (re-extracting
    on top of an already-written record) and reds the `is None` row below.
    """
    _force_ack(monkeypatch)
    session_id = "s-14232-raised-phase"

    with _ParkedExtractor() as parked:
        r = client.post("/v1/sessions", json={
            "conversation": _CONV, "session_id": session_id})
        assert r.status_code == 200, r.text
        assert parked.started.wait(30.0), "the tail never reached the extractor"

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            row = _session_row(session_id)
            if row and row[0][3] == "extracting":
                break
            time.sleep(0.02)
        assert row and row[0][3] == "extracting", (
            f"precondition: the parked tail must be publishing a live phase: {row}")

        class _Failed:
            def cancelled(self) -> bool:
                return False

            def exception(self):
                return RuntimeError("the extractor raised behind the ACK")

        state = {"attempted": True, "finalized": False, "lane": "v2"}
        ha._capture_tail_failed(_Failed(), _proj(), session_id, state)

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            row = _session_row(session_id)
            if row and row[0][3] is None:
                break
            time.sleep(0.02)
        assert row and row[0][3] is None and row[0][4] is None, (
            f"a raised tail must retire the phase pair, got {row}")
        assert row[0][1] is None, (
            "…and must NOT record an outcome: the raise arm keeps its "
            f"NULL→legacy-replay shape, got capture_ok={row[0][1]!r}")
        block = client.get(f"/v1/sessions/{session_id}").json()["extraction"]
        assert block["outcome_known"] is False, block
        assert "phase" not in block, (
            f"nothing is running, so no phase may be published: {block}")
        parked.release.set()
    _drain_tails()


# ── R3 / R3b: what the ACK body says, and what it refuses to say ──────────

def test_the_ack_body_cannot_be_read_as_extracted_nothing(client, monkeypatch):
    """R3 — the `#12362` defect class, at the wire.

    ⛔ Key PRESENCE is not the check (it is the property the change preserves):
    the assertions are on VALUES, and on the marker the CLI and the spool drain
    already read. A body that satisfied key presence while carrying
    `extraction_mode="no-provider"` or a non-empty `errors` list would print the
    honest sentence AND write a priced re-extraction deferral — offering to
    re-extract work that is running right now.
    """
    _force_ack(monkeypatch)
    from tortoise.capture_spool import (
        UNRECORDED_OUTCOME_KEY,
        UNRECORDED_OUTCOME_VALUE,
    )
    from tortoise.session_projection import SESSION_EXTRACTION_PHASES

    with _ParkedExtractor() as parked:
        r = client.post("/v1/sessions", json={
            "conversation": _CONV, "session_id": "s-14232-ack-body"})
        assert r.status_code == 200, r.text
        assert parked.started.wait(30.0)
        body = r.json()

        # The protocol envelope: `accepted` is the vocabulary's own member for
        # "durable, async-confirmed", and this is its first emission — so the
        # assertion is explicit rather than implied.
        assert body["protocol_version"] == "memory_write_v1", body
        assert body["status"] == "accepted", body
        # The record-phase facts the client can still rely on.
        assert body["session_id"] == "s-14232-ack-body"
        assert body["turns"] == len(_CONV)
        # The count is SO FAR, and every in-flight signal agrees.
        assert body["extracted"] == 0 and body["points"] == [], body
        assert body["surfaced"] == []
        assert body["errors"] == [], (
            "a non-empty errors list is what makes `_degraded_extraction` offer a "
            "re-extraction — an in-flight capture must not carry one")
        assert body["extraction_mode"] is None, (
            "a store-only mode here would print 'no memory points were extracted' "
            "for a capture that is extracting right now")
        assert body["extraction"]["status"] == "stored", body["extraction"]
        assert body["extraction"]["outcome_known"] is False, body["extraction"]
        assert body["extraction"]["phase"] in SESSION_EXTRACTION_PHASES, body
        # The client's OWN marker — the one the CLI and the spool drain read.
        assert body[UNRECORDED_OUTCOME_KEY] == UNRECORDED_OUTCOME_VALUE, (
            "the ack must carry the unrecorded-outcome marker, or `tortoise "
            "session capture` reads extracted:0 as 'nothing was extracted' and "
            "writes a priced deferral (#12362)")
        # …and the poll address is real, and answers.
        assert body["status_url"] == "/v1/sessions/s-14232-ack-body"
        detail = client.get(body["status_url"])
        assert detail.status_code == 200, detail.text
        assert detail.json()["extraction"]["phase"] == "extracting", (
            "the status surface must say what the ACK said — one state, two "
            f"readers: {detail.json()['extraction']}")
        parked.release.set()
    _drain_tails()


def test_completion_inside_the_window_still_returns_todays_receipt(client):
    """R3b — the in-budget half. No forcing of the join: it must land.

    With the suite's OPENED window (see the autouse
    `_capture_ack_window_is_deterministic` fixture: `_CAPTURE_ACK_DEADLINE_S` is
    `_CAPTURE_ACK_WINDOW_S` for the whole suite, because whether a local mock
    capture beats a 0.5 s deadline is a question about HOST LOAD and `#5049` rule 1
    forbids a deadline expiry from producing a verdict) and the offline mock
    extractor, completion lands inside it and the caller gets the SAME body shape
    as before this
    change: `status ok/partial`, a real `extracted` count, the extraction mode,
    and NO in-flight phase. That is what keeps 34 test files and every capture
    client reading what they read yesterday — and it is asserted here by cause
    (the window was not forced) rather than by timing the test.

    ⛔ The PRODUCTION deadline is exercised by
    `test_the_in_budget_receipt_survives_the_production_deadline` below, not
    here: this row is the shape assertion, that row is the deadline assertion.
    """
    r = client.post("/v1/sessions", json={
        "conversation": _CONV, "session_id": "s-14232-in-budget"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in ("ok", "partial"), body
    assert body["extracted"] >= 1, body
    assert body["extraction_mode"] == "llm:mock", body
    # ⛔ The in-budget body is TODAY'S body, unchanged — including the ABSENCE of
    # the ack's `extraction` block. That is not an omission: the block exists to
    # describe an outcome that is not yet known, and here it IS known, so the real
    # count and mode carry the whole story. Adding the block here would also have
    # to be added to `sdk.capture_session` to keep #4014's byte-parity, i.e. it
    # would stop being a REST-only additive change.
    assert "extraction" not in body, (
        f"the in-budget receipt must keep today's shape: {sorted(body)}")
    assert "first_capture" in body, (
        f"…and today's fields, which the ack cannot know: {sorted(body)}")
    assert "extraction_outcome" not in body, (
        "a completed capture's outcome IS known — the unrecorded marker must be "
        "absent, not false")


def test_the_in_budget_receipt_survives_the_production_deadline(
        client, monkeypatch):
    """F4 — the PRODUCTION window (0.5 s), not the suite's opened one.

    Every other row in this module runs with the autouse fixture's opened
    deadline, so without this one the constant the DEPLOYED handler uses is
    exercised by nothing: a value too tight for the joined receipt would keep
    every suite green while production clients silently stopped getting today's
    body. `_force_join` pins the deadline to the production value and leaves
    `_capture_ack_grace_s` real, so the join has to land on its own merits.

    ⛔ THE DEADLINE IS NOT THE ONLY CLOCK IN THIS ROW, and round 4 measured the
    consequence: pinning the production 0.5 s while asserting that gates + write
    + extraction fit inside it makes the red UNAttributable, because the same
    seam is documented in `tests/conftest.py:2150` as taking **0.008 s … 0.95 s**
    on one host depending on what ran before it — `0.95 s > 0.5 s`. A red would
    then assert a product conclusion ("that deadline is too tight") from a host
    event, which `#5049` rule 1 forbids.

    So this row MEASURES first and only then produces a verdict: the warm-up
    capture (which also pays the mock seam's import/first-call cost, a cost the
    deadline is not a claim about) is timed end to end, and it decides whether
    the DEFAULT verdict is attributable. On a host that can carry the claim the
    production deadline is asserted in full. On one that cannot, the row does
    not SKIP — a skip observes nothing — and it does not claim the deadline
    verdict either. It asserts what holds on BOTH arms: each body is one of the
    two documented shapes and never a half-shape, and either way the capture
    still reaches its terminal record with the in-flight phase cleared. That
    last leg is what a regression would actually red, and it is load-independent
    — which is why the row is worth running on every host rather than only on
    an idle one.
    """
    # ⛔ THE DEPLOYED VALUE IS READ AT IMPORT, not here: the suite's autouse
    # fixture has already opened `ha._CAPTURE_ACK_DEADLINE_S` by the time
    # any test body runs (that is what keeps the other rows off a wall clock), so
    # reading the live attribute would compare the fixture's value with itself.
    # Review round 5 measured the consequence of getting this wrong: a literal
    # here left this module GREEN while `hosted_api._CAPTURE_ACK_DEADLINE_S` was
    # changed `0.5 → 0.05`, i.e. the row certified a constant it never read.
    assert _PRODUCTION_ACK_DEADLINE_S == 0.5, (
        "the deployed ack window moved. This row is written against 0.5 s; if the "
        "constant is now a different number, re-derive the row rather than letting "
        f"it certify a value nobody deploys: {_PRODUCTION_ACK_DEADLINE_S}")
    started = time.monotonic()
    warm = client.post("/v1/sessions", json={
        "conversation": _CONV, "session_id": "s-14232-prod-window-warm"})
    assert warm.status_code == 200, warm.text
    _drain_tails()
    warm_s = time.monotonic() - started
    # Half the production window: below this there is room to attribute a miss to
    # the deadline, above it the miss would name the host.
    attributable = warm_s <= 0.25

    _force_join(monkeypatch, _PRODUCTION_ACK_DEADLINE_S)
    ack_started = time.monotonic()
    r = client.post("/v1/sessions", json={
        "conversation": _CONV, "session_id": "s-14232-prod-window"})
    ack_s = time.monotonic() - ack_started
    assert r.status_code == 200, r.text
    body = r.json()
    joined = "extraction" not in body
    if attributable:
        assert ack_s < _PRODUCTION_ACK_DEADLINE_S, (
            f"the in-budget receipt must land INSIDE the window it is named for: "
            f"ack took {ack_s * 1000:.0f} ms of {_PRODUCTION_ACK_DEADLINE_S} s "
            f"(a warm capture on this host measured {warm_s * 1000:.0f} ms)")
        assert joined, (
            "the join missed the PRODUCTION 0.5 s deadline on an offline mock "
            f"capture — that deadline is too tight: {sorted(body)}")
    else:
        # ⛔ NOT A SKIP, because the invariants below hold on BOTH arms and are
        # worth observing on every host. A miss here cannot be attributed to the
        # deadline (`#5049` rule 1), so the row does not CLAIM that verdict — but
        # it still refuses a half-shape: `accepted` may not carry a real count,
        # a receipt may not carry the ack's block, and the deferred arm must keep
        # the client's unrecorded-outcome marker (`#12362`).
        from tortoise.capture_spool import (
            UNRECORDED_OUTCOME_KEY,
            UNRECORDED_OUTCOME_VALUE,
        )
        if not joined:
            assert body["status"] == "accepted", body
            assert body["extracted"] == 0 and body["points"] == [], body
            assert body["extraction_mode"] is None, body
            assert body["extraction"]["outcome_known"] is False, body["extraction"]
            assert body[UNRECORDED_OUTCOME_KEY] == UNRECORDED_OUTCOME_VALUE, body
    if joined:
        assert body["status"] in ("ok", "partial"), body
        assert body["extracted"] >= 1, body

    # ⛔ WHICHEVER ARM RAN, the production window bounds the ANSWER and never the
    # work: the record must still reach its terminal state. This assertion is
    # load-independent, and it is the one a regression would actually red.
    _drain_tails()
    row = _session_row("s-14232-prod-window")
    assert row and row[0][1] is True, (
        f"the join deadline must never decide whether the capture COMPLETES — "
        f"arm={'joined' if joined else 'deferred'}, record={row}")
    assert row[0][3] is None and row[0][4] is None, (
        f"the terminal record must clear the in-flight phase: {row}")


# ── R4: the in-flight key outlives the tail ──────────────────────────────

def test_a_same_session_repost_during_the_tail_is_refused(client, monkeypatch):
    """`#3129` — the early ACK must not re-open the replay window.

    The ACK is served while the tail still owns the capture. A second POST for the
    same session in that window used to be admitted and replayed a 200 with 0
    extracted, for a capture whose only real attempt could then fail.
    """
    _force_ack(monkeypatch)
    session_id = "s-14232-in-flight"

    with _ParkedExtractor() as parked:
        first = client.post("/v1/sessions", json={
            "conversation": _CONV, "session_id": session_id})
        assert first.status_code == 200, first.text
        assert parked.started.wait(30.0)

        second = client.post("/v1/sessions", json={
            "conversation": _CONV, "session_id": session_id})
        assert second.status_code == 409, second.text
        assert "already in flight" in second.text, second.text
        parked.release.set()
    _drain_tails()

    # …and once the tail is done the session is free again (a re-POST replays).
    third = client.post("/v1/sessions", json={
        "conversation": _CONV, "session_id": session_id})
    assert third.status_code == 200, third.text
    assert third.json()["extraction_mode"] == "replayed", third.json()


def test_a_same_session_repost_during_the_REPORT_half_is_refused(
        client, monkeypatch):
    """`#3129`'s actual residual window, which only `tail_pending()` protects.

    ⛔ Review round 4 measured that the row ABOVE cannot red on the mutant it used
    to name. It parks the MODEL CALL, so at the moment of the second POST
    `_CaptureSlot._done` is still False and `_release_session`'s
    `if self._handed_off and not (self._done and self._request_done): return`
    refuses the pop on its own — `hand_off()` alone already yields the 409, and
    deleting `tail_pending()` changes nothing.

    The window `tail_pending()` exists for is the NEXT one: extraction has
    RETURNED (`worker_done` ran, `_done` is True, `_request_done` is True because
    the ACK went out immediately) while `_report_capture` is still writing the
    provenance stamp / entity links / `capture_ok`. In that window only
    `_tail_done` still holds the key — and a retry admitted there would read
    `capture_ok = NULL` and replay a 200 over a tail that is still running.

    So this row parks the REPORT half (`read_stages.mark` at the
    `STAGE_CAPTURE_TAIL` mark, the first thing `_report_capture` does after the
    model call) and holds the second POST in that exact window. Delete
    `slot.tail_pending()` and the KEY IS GONE before the report half even starts
    (`worker_done` runs when the extraction returns, and with no pending tail it
    releases), so this row reds on its own precondition — "the session key must
    still be held while the REPORT half runs" — rather than on the 409 below.
    ⛔ The mechanism is the precondition firing, and that is the honest statement:
    an earlier draft of this docstring said "you get a 200 replay", which round 5
    measured to be false (the row fails earlier and for a clearer reason).
    """
    _force_ack(monkeypatch)
    session_id = "s-14232-report-window"
    real_mark = ha._mark_capture_stage
    entered = threading.Event()
    release = threading.Event()

    def _parked_mark(stage, *args, **kwargs):
        real_mark(stage, *args, **kwargs)
        if stage == ha.STAGE_CAPTURE_TAIL:
            entered.set()
            assert release.wait(30.0), "test never released the report half"

    monkeypatch.setattr(ha, "_mark_capture_stage", _parked_mark)
    try:
        first = client.post("/v1/sessions", json={
            "conversation": _CONV, "session_id": session_id})
        assert first.status_code == 200, first.text
        assert entered.wait(30.0), (
            "the tail never reached the REPORT half — the window this row is "
            "about does not exist")
        # Precondition, and the whole reason this row discriminates: the worker
        # is DONE (`_done`) and the request is DONE, so only `_tail_done` holds
        # the key.
        assert _key_held(session_id), (
            "the session key must still be held while the REPORT half runs — the "
            f"in-flight registry is {ha._CAPTURE_SESSIONS}")

        second = client.post("/v1/sessions", json={
            "conversation": _CONV, "session_id": session_id})
        assert second.status_code == 409, (
            "a retry during the REPORT half must be refused: the extraction has "
            "returned but the outcome is not written yet, so a replay would "
            f"answer over a live tail — got {second.status_code} {second.text}")
        assert "already in flight" in second.text, second.text
    finally:
        release.set()
    _drain_tails()

    # …and the key IS released once the completion reports (the row above would
    # also pass with a key that is never released at all).
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and _key_held(session_id):
        time.sleep(0.02)
    assert not _key_held(session_id), (
        "the completion must release the session key: a key that outlives the "
        f"tail refuses every later capture of this session forever: "
        f"{ha._CAPTURE_SESSIONS}")


# ── R5: the phase, and the stage the next 504 will name ──────────────────

def test_the_phase_moves_queued_to_extracting_and_then_clears(client, monkeypatch):
    """#2952 / #14232 — a queued session SAYS SO, everywhere it is read."""
    _force_ack(monkeypatch)
    session_id = "s-14232-phase"

    with _ParkedExtractor() as parked:
        r = client.post("/v1/sessions", json={
            "conversation": _CONV, "session_id": session_id})
        assert r.status_code == 200, r.text
        assert parked.started.wait(30.0)

        # The worker is INSIDE the model call: the record says `extracting`.
        detail = client.get(f"/v1/sessions/{session_id}").json()
        assert detail["extraction"]["phase"] == "extracting", detail["extraction"]
        assert detail["extraction"]["outcome_known"] is False
        listed = client.get("/v1/sessions").json()["sessions"]
        row = next(s for s in listed if s["id"] == session_id)
        assert row["extraction"]["phase"] == "extracting", (
            "the LIST read must agree with the detail read — one derivation, "
            f"three surfaces: {row['extraction']}")
        # ⛔ THE THIRD SURFACE: the SDK read. The parity row
        # (`test_hosted_api.py::test_sdk_session_read_parity_with_the_hosted_projection`)
        # captures through the INLINE lane — a terminal row, where the phase is
        # absent on BOTH sides — so it cannot red a deleted or mis-indexed phase
        # leg in `sdk._read_captured_session`. This is the only row that reads an
        # IN-FLIGHT session through the SDK: deleting `phase=sess[13]` or reading
        # the neighbouring index reds it here.
        sdk_read = ha._make_sdk(namespace=TEST_ORG_ID)._get_session(session_id)
        assert sdk_read is not None, (
            "the SDK must read back a session the handler just captured")
        sdk_block = sdk_read["extraction"]
        assert sdk_block["phase"] == "extracting", (
            "the SDK read must carry the phase the hosted pair carries — one "
            f"derivation, three surfaces: {sdk_block}")
        assert sdk_block["outcome_known"] is False, sdk_block
        assert sdk_block["phase_at"] == detail["extraction"]["phase_at"], (
            "the phase TIMESTAMP leg is the index-sensitive half: a wrong "
            f"positional would put it under another key: {sdk_block}")
        parked.release.set()
    _drain_tails()

    detail = client.get(f"/v1/sessions/{session_id}").json()
    assert detail["extraction"]["outcome_known"] is True, detail["extraction"]
    assert "phase" not in detail["extraction"], (
        f"terminal rows publish no phase: {detail['extraction']}")


def test_a_504_on_this_path_names_its_stage(client, monkeypatch):
    """Step 8 — the diagnosis that production did not have.

    Measured on the deployment: `transport wait bound (10s) exceeded … (stage=
    unmarked spent_ms={})`. The refusal body is FLAT (`stage`, `stages_ms`), and
    `stages_ms` carries only stages that FINISHED, so a breach INSIDE the record
    phase must appear in `stage`.

    ⛔ WHAT THIS ROW DOES **NOT** DO (review round 7, P2 — the docstring used to
    claim it): deleting the `capture_write` mark is NOT caught here. It falls back
    to `capture_gates`, which this row reports as INCONCLUSIVE by design (see the
    skip arm), so the mutant is caught by
    `test_the_write_mark_sits_between_the_gates_and_the_extraction`. This row
    pins the WIRE SHAPE of a genuine refusal — flat body, a named stage, a timed
    gate phase, and the bound's own error text — which is what a client reads.
    """
    # ⛔ RESTORED, NOT ADDED (review round 8, P1): the commit that rewrote the
    # docstring above ALSO deleted this import while declaring the change
    # docstring-only, which is why the mechanical AST check could not pass. It is
    # inert here — that is what the `noqa` absorbs — and it stays so the delta is
    # literally what it says it is.
    from tortoise.session_projection import SESSION_EXTRACTION_PHASES  # noqa: F401

    # ⛔ THE GATES PHASE MUST BE CHEAP FOR THIS ROW TO BE ATTRIBUTABLE (#5049
    # rule 1, `tests/conftest.py`'s own fixture docstring). The bound below is
    # 3.0 s and the write is made to overrun it; the gates phase runs FIRST, and
    # on an un-stubbed tree it contains `count_org_usage(org, "points")` — a
    # FULL-GRAPH scan (`hosted_api.py:20796`, which this PR's plan calls out as
    # pre-existing and out of scope). When that scan alone exceeds the bound the
    # refusal correctly names `capture_gates` instead of the write, and this row
    # reports that as INCONCLUSIVE rather than red (the skip arm below) — the red
    # would be about the host, not about the `capture_write` mark it exists to
    # pin. Measured at the round-4 head, then bound 0.35 s: 4 failed / 1 passed
    # with `{'stage': 'capture_gates', 'stages_ms': {'pre_handler': 24.1}}`.
    # Stubbing the quota read to a constant leaves ONE thing able to cross the
    # bound: the deliberately slow write.
    #
    # ⛔ AND IT WAS STILL NOT ENOUGH (measured at the round-4 head, with the stubs
    # above in place): the row reddened `stage=capture_gates` with
    # `stages_ms={'pre_handler': 70.5}` — the pre-handler work finished in 70 ms
    # and the request then sat for the rest of the 0.35 s BEFORE the endpoint
    # marked `capture_gates`, so a 0.35 s bound is simply shorter than this box's
    # cold start on a freshly-created graph. A wider margin makes the row
    # attributable WITHOUT weakening it: the breach still lands inside the write,
    # because the write is the only leg made to outlast the bound.
    monkeypatch.setattr(ha, "_session_extraction_estimate", lambda *_a, **_kw: 0)
    import tortoise.quota as _quota
    monkeypatch.setattr(_quota, "count_org_usage",
                        lambda *_a, **_kw: 0)
    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 3.0)
    real_write = ha._write_session_and_turns

    # ⛔ RECORD the marks as well as slow the write down. The bound is a WALL CLOCK
    # and the gates phase has SEVEN awaits in it (#5049 rule 1; review round 5
    # measured this row red 1-in-6 at load ≈ cpus even with the quota read
    # stubbed). The mark's EXISTENCE and its ORDER are properties of the code, so
    # they are pinned deterministically by
    # `test_the_write_mark_sits_between_the_gates_and_the_extraction` below. This
    # row keeps the end-to-end half and reports INCONCLUSIVE when the payload says
    # the breach did not land after the write mark — that outcome says nothing
    # about the mark, whichever way the box was loaded.
    marks: list[tuple[str, float]] = []
    real_mark = ha._mark_capture_stage

    def _recording_mark(stage, *args, **kwargs):
        marks.append((stage, time.monotonic()))
        return real_mark(stage, *args, **kwargs)

    monkeypatch.setattr(ha, "_mark_capture_stage", _recording_mark)

    def _slow_write(*args, **kwargs):
        time.sleep(5.0)
        return real_write(*args, **kwargs)

    monkeypatch.setattr(ha, "_write_session_and_turns", _slow_write)

    # ⛔ A RAW client, not the `client` fixture. That fixture is a `#5049` VERDICT
    # client: it converts a transport-bound refusal into an INCONCLUSIVE skip,
    # which is exactly right for every other test and exactly wrong for this one,
    # whose property IS the refusal. The fixture is still requested so its DB
    # pin, patched SDK constructor, auth override and consent seed are in place —
    # the raw client runs against the same `ha.app`.
    from fastapi.testclient import TestClient

    with TestClient(ha.app, raise_server_exceptions=False) as raw:
        r = raw.post("/v1/sessions", json={
            "conversation": _CONV, "session_id": "s-14232-slow"})
    assert r.status_code == 504, r.text
    body = r.json()
    if body["stage"] != "capture_write":
        # The gates phase spent the whole transport bound on this host, so the
        # refusal names `capture_gates` — CORRECT behaviour, and no evidence about
        # the write mark. INCONCLUSIVE, never a red that blames the code under
        # test (#5049 rule 1); the mark itself is pinned deterministically below.
        pytest.skip(
            "INCONCLUSIVE on this host: the breach landed in "
            f"stage={body['stage']!r} with stages_ms="
            f"{body.get('stages_ms')} and marks={[s for s, _ in marks]} — the "
            "gates phase spent the transport bound, so this refusal cannot be "
            "attributed to the write mark (#5049 rule 1). Not a red, not a pass.")
    assert body["stage"] == "capture_write", (
        f"the breach must name the stage it landed in: {body}")
    assert body["stages_ms"].get("capture_gates") is not None, (
        f"the finished gate phase must be timed beside it: {body}")
    # The recorder is LOAD-BEARING here, not decoration (review round 6, P2): a
    # refusal that names `capture_write` can only have got there past the gates
    # mark, so this assertion is implied by the one above and cannot red from
    # load — while a deleted gates mark is caught by the row above
    # (`test_the_write_mark_sits_between_the_gates_and_the_extraction`). On the
    # skip arm the same list rides in the skip reason, which is the diagnostic a
    # reader needs to see WHY the breach landed where it did.
    assert "capture_gates" in [s for s, _ in marks], (
        f"marks seen before the breach: {[s for s, _ in marks]}")
    # ⛔ THE BOUND'S OWN ENVELOPE, MEASURED — not a guess about the body's shape.
    # A breaching request carries `detail` = the transport bound's static sentence
    # (`_send_wait_bound_refusal`: `{"detail": _TRANSPORT_WAIT_BOUND_MESSAGE,
    # **stage_fields}`), so `detail` IS present on this path. Review round 5's F1
    # premise ("the refusal body carries no `detail` at all", so the row should
    # read `"detail" not in body`) was measurably wrong: applied, it reds on a 504
    # that is behaving exactly as shipped. The falsifiable property is the
    # sentence's IDENTITY — the refusal names the BOUND, never the caller's
    # exception text and never generic prose. The mutant that reds it is folding
    # `detail` from the raised exception. ⛔ "or swap the constant for new wording"
    # used to be advertised here and was FALSE (review round 8, P2): the assertion
    # reads the SAME constant the refusal emits, so rewording it moves both sides
    # and stayed green — a second advertised-but-absent check, in the comment of
    # the row that replaced the first one. The identity is what it pins; if the
    # wording must be pinned too, pin a literal fragment beside this line.
    assert body.get("detail") == ha._mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE, (
        "a stage refusal must carry the transport bound's own sentence beside its "
        f"stage envelope, not a caller-specific or generic message: {body}")


def test_the_write_mark_sits_between_the_gates_and_the_extraction(
        client, monkeypatch):
    """Step 8's DETERMINISTIC half: the stage marks exist, and they are ORDERED.

    `test_a_504_on_this_path_names_its_stage` can only be as attributable as the
    wall clock allows, and review round 5 measured that its gates phase carries
    seven awaits — on a loaded host the breach legitimately lands in
    `capture_gates`, where the row must report INCONCLUSIVE. What is NOT a matter
    of luck is the property production did not have: this path MARKS the stage it
    is in, in order, so a refusal can name its cause instead of `unmarked`/`{}`.

    This row asserts the order from the marks the handler emits on one ordinary
    capture — `capture_gates` → `capture_write` → later stages — with no transport
    bound, no sleep and no deadline in play. Delete
    `_mark_capture_stage(STAGE_CAPTURE_WRITE)` and `capture_write` is absent: that
    is the mutant the row above names and cannot always attribute.
    """
    _force_ack(monkeypatch)
    seen: list[str] = []
    real_mark = ha._mark_capture_stage

    def _recording_mark(stage, *args, **kwargs):
        seen.append(stage)
        return real_mark(stage, *args, **kwargs)

    monkeypatch.setattr(ha, "_mark_capture_stage", _recording_mark)
    r = client.post("/v1/sessions", json={
        "conversation": _CONV, "session_id": "s-14232-mark-order"})
    assert r.status_code == 200, r.text
    _drain_tails()

    assert "capture_gates" in seen, f"the gates phase must be marked: {seen}"
    assert "capture_write" in seen, (
        "the write phase must be marked — without it a transport-bound refusal on "
        f"this path falls back to naming the gates phase: {seen}")
    assert seen.index("capture_gates") < seen.index("capture_write"), (
        f"the write mark must come AFTER the gates mark: {seen}")
    later = [s for s in seen if s not in ("capture_gates", "capture_write")]
    assert later, (
        f"the path must go on to mark its later stages too: {seen}")


# ── R6: capacity always comes back ───────────────────────────────────────

def test_capacity_returns_after_ack_and_completion(client, monkeypatch):
    """The slot is reserved at admission and owned by phases; it must always return.

    Measured on the leak direction that is SILENT: a decrement with nothing in
    flight logs a warning, but a capacity that is never returned does not — it
    just makes every later capture 429, forever.
    """
    _force_ack(monkeypatch)
    before = ha._CAPTURE_IN_FLIGHT

    r = client.post("/v1/sessions", json={
        "conversation": _CONV, "session_id": "s-14232-capacity"})
    assert r.status_code == 200, r.text
    _drain_tails()

    assert before == ha._CAPTURE_IN_FLIGHT, (
        f"capture capacity leaked: was {before}, now {ha._CAPTURE_IN_FLIGHT}")
    assert not ha._CAPTURE_SESSIONS, ha._CAPTURE_SESSIONS


def test_a_completion_that_dies_before_submitting_still_returns_capacity(
        client, monkeypatch):
    """R6's sharp edge: `hand_off()` moves capacity to the TASK, not to a future.

    If the completion dies before `_run_capture_bounded` submits — a dispatch
    failure (`_submit_off_loop` raises once the pool is shut down or a worker
    cannot start), a cancel at the first await — nothing else would ever run
    `worker_done`. Binding the release to the TASK's completion is what makes this
    pass; binding it only to the extraction future leaks the slot, and every later
    capture in the process 429s.
    """
    _force_ack(monkeypatch)
    before = ha._CAPTURE_IN_FLIGHT

    def _no_dispatch(*_a, **_k):
        raise RuntimeError("pool is shut down (simulated)")

    # `_run_capture_bounded` is the EXTRACTION's dispatch (the record phase uses
    # `_run_off_loop`), so patching it fails the tail and nothing else — the
    # failure lands at the first await INSIDE the task, which is the "dies before
    # submitting" shape exactly.
    monkeypatch.setattr(ha, "_run_capture_bounded", _no_dispatch)
    r = client.post("/v1/sessions", json={
        "conversation": _CONV, "session_id": "s-14232-no-dispatch"})
    # The ACK already went out; the completion fails afterwards.
    assert r.status_code == 200, r.text
    _drain_tails()

    assert before == ha._CAPTURE_IN_FLIGHT, (
        f"capacity leaked on the no-dispatch arm: {before} -> {ha._CAPTURE_IN_FLIGHT}")
    assert not ha._CAPTURE_SESSIONS, ha._CAPTURE_SESSIONS
    row = _session_row("s-14232-no-dispatch")
    assert row and row[0][1] is None, (
        "a dispatch FAILURE is not a cancellation and must not write the "
        f"abandoned marker (raise-shape contract): {row}")
    # ⛔ THE PHASE RETIRE'S WIRING PIN. This is the only row that reaches
    # `_capture_tail_failed` through the REAL task — the two window rows drive the
    # callback by hand — so a mutant that stops a failed tail from ever reaching
    # the clear left every row green while a real failed capture kept
    # `phase: extracting` forever (measured: deleting the raise-arm submit reds
    # this assertion plus the hand-driven row, nothing else). The `extracting`
    # write lands before the dispatch, this dispatch raises, the tail dies.
    row = _await_phase_gone("s-14232-no-dispatch")
    assert row[0][3] is None and row[0][4] is None, (
        "a tail that dies between its phase write and its terminal record must "
        f"still retire the phase through the real callback wiring: {row}")


def test_a_failed_terminal_write_still_retires_the_phase(client, monkeypatch):
    """A terminal SET that RAISES must not leave the phase behind.

    MEASURED GAP this closes: `state["finalized"]` was set unconditionally after
    the terminal `capture_ok` write, INCLUDING the arm where that write raised.
    `finalized` is what suppresses the callback's phase retire, so a failed
    terminal write left `phase: extracting` beside a `capture_ok` that was never
    written — permanently, and reverting either half of the fix reds this row.

    Drives the REAL handler: the terminal statement is recognised by BOTH of its
    own clauses (`s.capture_ok=$ok` AND the phase clear this PR adds, because the
    first alone also matches the SDK's stamp), so this fails the outcome write and
    nothing else — the
    record write, the phase writes, the extraction and the reads all still run.
    `capture_ok` must stay NULL (nothing was recorded, so the raise-shape/replay
    contract decides what the next POST does) and the phase must be gone.
    """
    _force_ack(monkeypatch)
    session_id = "s-14232-terminal-write-fails"
    graph_cls = type(_proj().g)
    real_query = graph_cls.query

    def _query_failing_the_terminal_set(graph_self, statement, *a, **kw):
        # ⛔ BOTH tokens: `s.capture_ok=$ok` alone also matches the SDK's
        # `_stamp_capture_attempt_state` (tortoise/sdk.py), so a future route through
        # that door would fail TWO statements and this row would still pass while its
        # docstring silently became false. The phase-clear clause is this PR's own.
        if ("s.capture_ok=$ok" in statement
                and "s.capture_extraction_phase=NULL" in statement):
            raise RuntimeError("terminal outcome write failed (simulated)")
        return real_query(graph_self, statement, *a, **kw)

    monkeypatch.setattr(graph_cls, "query", _query_failing_the_terminal_set)

    r = client.post("/v1/sessions", json={
        "conversation": _CONV, "session_id": session_id})
    assert r.status_code == 200, r.text
    _drain_tails()

    row = _await_phase_gone(session_id)
    assert row and row[0][1] is None, (
        "the terminal write FAILED, so no outcome was recorded — `capture_ok` "
        f"must stay NULL, got {row}")
    assert row[0][3] is None and row[0][4] is None, (
        "a failed terminal write must still retire the phase it left behind, or "
        "the session reports `extracting` (and PENDING forever) beside an "
        f"outcome that was never recorded: {row}")


# ── review findings, now pinned ────────────────────────────────────────────

def test_a_cancelled_request_during_the_join_returns_capacity_and_the_key(
        client, monkeypatch):
    """P1 — a cancel BEFORE the extraction is submitted must not leak the slot.

    The deferred extraction has TWO awaits before it reaches
    `_run_capture_bounded`: the `extracting` phase write and the tenant-vocabulary
    compile. A cancellation in that window kills the task with no pool future to
    report, and the request's own `hand_off()`/`tail_pending()` already retired its
    claim — so before the review fix nothing ever released capacity or the session
    key: `_CAPTURE_IN_FLIGHT` stayed at its reserved value and the session id 409'd
    for the life of the process (silent in the one direction nobody can see:
    capacity ratchets to the ceiling and every later capture 429s).

    ⛔ Asserted on the ACCOUNTING, not on a response: the request is cancelled, so
    there is no response to read. `_CAPTURE_SESSIONS` is the same registry the 409
    reads.
    """
    import asyncio

    # The shared async seam (#8582/#5049): a wait-bound refusal reaching THIS
    # client is a non-answer (INCONCLUSIVE), never an assertion about the code.
    from tests.test_capture_loop_responsiveness import (
        _verdict_async_client,
    )
    from tests.test_hosted_api import TEST_TEAM
    from tortoise.hosted_api import app

    before = ha._CAPTURE_IN_FLIGHT
    parked = threading.Event()          # the phase write is IN FLIGHT
    release = threading.Event()         # …and stays there until we say so

    def _parked_phase(_proj, _session_id, phase, _at):
        """Park ONLY the `extracting` write — the one the EXTRACTION task makes.

        ⛔ Not every phase write: the footer writes `queued` from the REQUEST,
        before the task exists and before `hand_off()`. Parking that one too makes
        the cancellation land before the split point, where the endpoint's own
        teardown releases everything — which is how the first version of this test
        passed against a build with the leak still in it (mutation-checked).
        """
        if phase != "extracting":
            return None
        parked.set()
        assert release.wait(30.0), "test never released the phase writer"
        return None

    monkeypatch.setattr(ha, "_write_capture_phase", _parked_phase)
    # A positive window, so the handler really is inside the join when the
    # cancellation lands (the default path patches it to 0 for the ack tests).
    monkeypatch.setattr(ha, "_capture_ack_grace_s", lambda _request: 5.0)

    payload = {"conversation": _CONV, "session_id": "s-14232-cancel-pre-submit",
               "harness": "pi"}

    async def _run():
        async with _verdict_async_client(app, base_url="http://test") as ac:
            task = asyncio.create_task(ac.post("/v1/sessions", json=payload))
            for _ in range(400):
                if parked.is_set():
                    break
                await asyncio.sleep(0.01)
            assert parked.is_set(), (
                "the extraction never reached its first await — this test would "
                "then be exercising the submitted window instead")
            task.cancel()  # the client goes away mid-join
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # the request may surface it
                pass
            for _ in range(400):  # the abandoned task's callback settles
                if before == ha._CAPTURE_IN_FLIGHT and not ha._CAPTURE_SESSIONS:
                    break
                await asyncio.sleep(0.02)
            return ha._CAPTURE_IN_FLIGHT, dict(ha._CAPTURE_SESSIONS)

    try:
        after_in_flight, after_sessions = asyncio.run(_run())
    finally:
        release.set()
        _drain_tails()

    assert after_in_flight == before, (
        f"capacity leaked on a cancel BEFORE the extraction was submitted: "
        f"{before} -> {after_in_flight} — every later capture in this process "
        f"now 429s, silently")
    assert not after_sessions, (
        f"the in-flight session key was never released: {after_sessions} — that "
        f"session id 409s forever")
    assert TEST_TEAM  # the router auth override this client inherits


def test_the_slot_guard_keeps_capacity_for_a_bound_WORKER(client):
    """The OTHER direction of `_capture_slot_finished`'s guard (#3060's ceiling).

    The row above proves the release fires when NO worker was bound — the silent
    LEAK direction. This one proves the guard's load-bearing half: when a pool
    thread owns the capture — `_run_capture_bounded` submitted it and bound
    `worker_done` to that future — the completion coroutine ending must NOT
    release the capacity out from under it. Releasing there frees a slot a parked
    thread still occupies, so the ceiling silently admits more concurrent captures
    than `_CAPTURE_MAX_IN_FLIGHT` allows.

    ⛔ Measured on the shape this closes: dropping the `if not slot.worker_bound():`
    guard (i.e. `slot.complete_tail(); slot.worker_done()` unconditionally) left
    BOTH capacity rows green — one finishes the worker first, the other never
    starts it — so the branch had no row that could red it.

    ⛔ Asserted on the SLOT ACCOUNTING, deliberately not end-to-end: the same
    property driven through a real POST + a parked extractor spends the record
    phase's cold lazy init first (a first-capture embedding-model load, measured
    >90 s in a fresh process on this box), which makes it a test of this machine's
    model cache rather than of this branch.
    """
    before = ha._CAPTURE_IN_FLIGHT

    # (i) a worker IS bound: capacity and the session key survive the tail ending.
    bound_key = f"{TEST_ORG_ID}:unit:unit-worker-bound"
    slot = ha._reserve_capture_slot(bound_key)
    assert before + 1 == ha._CAPTURE_IN_FLIGHT, "the reservation must be held"
    slot.hand_off()             # the request hands the capture to the worker…
    slot.tail_pending()         # …the deferred lane holds the SESSION KEY…
    slot.mark_worker_bound()    # …and `_run_capture_bounded` submitted it
    slot.release()              # the request's own teardown, at the early ACK
    ha._capture_slot_finished(slot)   # the completion coroutine ends
    assert before + 1 == ha._CAPTURE_IN_FLIGHT, (
        "the completion coroutine must NOT release capacity a parked pool thread "
        f"still occupies (#3060): {before} -> {ha._CAPTURE_IN_FLIGHT}")
    assert bound_key in ha._CAPTURE_SESSIONS, (
        "the session key must outlive the tail too — a same-session retry "
        f"admitted now would replay over it: {dict(ha._CAPTURE_SESSIONS)}")

    # …and when the worker really reports, everything comes back.
    slot.worker_done()
    assert before == ha._CAPTURE_IN_FLIGHT, ha._CAPTURE_IN_FLIGHT
    assert bound_key not in ha._CAPTURE_SESSIONS, ha._CAPTURE_SESSIONS

    # (ii) NO worker was bound: the completion must release both, or the leak is
    # the silent one (capacity ratchets to the ceiling, the id 409s forever).
    free_key = f"{TEST_ORG_ID}:unit:unit-no-worker"
    slot = ha._reserve_capture_slot(free_key)
    slot.hand_off()
    slot.tail_pending()
    slot.release()
    ha._capture_slot_finished(slot)
    assert before == ha._CAPTURE_IN_FLIGHT, (
        f"an unbound completion must release the reservation: {before} -> "
        f"{ha._CAPTURE_IN_FLIGHT}")
    assert free_key not in ha._CAPTURE_SESSIONS, ha._CAPTURE_SESSIONS


def test_a_retry_in_flight_says_so_on_the_ack_AND_on_the_status_url(
        client, monkeypatch):
    """P2 — on the #2335 TRUE-retry lane the two surfaces must agree.

    A retry re-extracts a session whose record ALREADY carries
    `capture_ok = False` (the record-phase MERGE never touches it), and it writes
    the in-flight phase. Before the review fix the ACK hardcoded `None` as the
    outcome — so it said `status: stored`, `outcome_known: false` — while the
    `status_url` it advertises derived `stored-extraction-failed` from the record
    and SUPPRESSED the phase (`capture_ok is None and phase`), i.e. the live retry
    was invisible on the one surface the ACK tells the client to poll, and the two
    disagreed about the same capture.
    """
    session_id = "s-14232-retry-lane"
    r = client.post("/v1/sessions", json={
        "conversation": _CONV, "session_id": session_id})
    assert r.status_code == 200, r.text
    _drain_tails()
    # Put the record in the state a FAILED v2 attempt leaves (`v2` is a
    # retry-eligible convergent lane, so the next POST is a TRUE retry).
    _proj().g.query(
        "MATCH (s:Session {id:$sid}) SET s.capture_ok=false, "
        "s.capture_extractor='v2'", params={"sid": session_id})

    _force_ack(monkeypatch)
    with _ParkedExtractor() as parked:
        r = client.post("/v1/sessions", json={
            "conversation": _CONV, "session_id": session_id})
        assert r.status_code == 200, r.text
        assert parked.started.wait(30.0), "the retry never re-extracted"
        ack = r.json()["extraction"]
        read = client.get(f"/v1/sessions/{session_id}").json()["extraction"]

        assert ack["status"] == "stored-extraction-failed", ack
        assert read["status"] == "stored-extraction-failed", read
        # …and BOTH say the outcome is not known yet, and name the live phase.
        for block in (ack, read):
            assert block["outcome_known"] is False, block
            assert block["phase"] in ("queued", "extracting"), block
        parked.release.set()
    _drain_tails()
    # …and once the retry finishes the phase is gone and the outcome is known.
    final = client.get(f"/v1/sessions/{session_id}").json()["extraction"]
    assert final["outcome_known"] is True, final
    assert "phase" not in final, final


# ── the test SUBSTRATE's own claim (review round 5, F2) ───────────────────

def test_the_suite_window_is_the_opened_window_not_the_half_bound():
    """The autouse fixture's claim, pinned: the CLAMP is opened with the window.

    `tests/conftest.py::_capture_ack_window_is_deterministic` opens
    `_CAPTURE_ACK_DEADLINE_S` to `_CAPTURE_ACK_WINDOW_S` so that no pre-existing
    "the POST body reports what was extracted" assertion races a deadline
    (`#5049` rule 1). That claim was FALSE: `_capture_ack_grace_s` clamps to
    `min(window, _TRANSPORT_WAIT_BOUND_S / 2)` — `min(300.0, 5.0)` at this suite's
    window, `min(30.0, 5.0)` at the earlier 30 s cut — = **5 s** either way, and a cold
    `build_master_list` is ~12 s, so the rows the fixture exists to protect still
    red on a cold shard — measured, and the reason two rows in this suite carry
    per-row warm-ups (review round 5).

    Mutant that reds this row: drop the `_capture_ack_ceiling_s` override from the
    fixture. The ceiling is then 5.0 s and the join the suite actually gets is
    5 s — not the window the fixture promises.

    ⛔ THE WINDOW MUST ALSO COVER A COLD RECORD PHASE, which is a separate fact and
    a second way this claim can be false: the grace is anchored on ARRIVAL, so a
    record phase longer than the window leaves the join zero time even with the
    clamp fully open. Measured on this box: the first capture in a process reached
    the join with `arrival_age = 123.6 s`. Hence the asserted value is the suite's
    own constant and the constant carries the cold-start margin — a literal here
    would silently re-open the race the moment the constant moved.
    """
    from tests.conftest import _CAPTURE_ACK_WINDOW_S

    assert ha._CAPTURE_ACK_DEADLINE_S == _CAPTURE_ACK_WINDOW_S, (
        "the fixture must open the window for the whole suite: "
        f"{ha._CAPTURE_ACK_DEADLINE_S} != {_CAPTURE_ACK_WINDOW_S}")
    assert _CAPTURE_ACK_WINDOW_S > 123.6, (
        "the suite window must exceed the MEASURED cold record phase (123.6 s at "
        "the join on this box, 2026-11-22), or the arrival-anchored grace is already "
        f"spent when the join starts: {_CAPTURE_ACK_WINDOW_S}s")
    ceiling = ha._capture_ack_ceiling_s()
    assert ceiling == ha._CAPTURE_ACK_DEADLINE_S, (
        f"the join's ceiling is {ceiling}s while the window is "
        f"{ha._CAPTURE_ACK_DEADLINE_S}s — the production clamp "
        "(`min(window, _TRANSPORT_WAIT_BOUND_S / 2)` = 5 s) is still in place, so a "
        "cold record phase or a cold vocabulary compile reds a pre-existing capture "
        "assertion on a deadline expiry (#5049 rule 1)")
    # …and the JOIN really uses it: a request that just arrived gets the window.
    import types

    request = types.SimpleNamespace(
        state=types.SimpleNamespace(_wait_bound_t0=time.monotonic()))
    assert ha._capture_ack_grace_s(request) > 250.0, (
        "the ceiling is open but the join's grace is not: "
        f"{ha._capture_ack_grace_s(request)}")


# ── the phase VIEW's one property (review round 5, F3) ────────────────────

def test_the_extraction_started_phase_write_precedes_the_ack_view(client,
                                                                  monkeypatch):
    """The RECORD is written before the ACK's view, and the view IS written.

    `_capture_extraction_started` exists so the deferred ACK can name the phase the
    RECORD carries. Review round 5 read the `view["phase"] = "extracting"`
    assignment as dead ("no await between `create_task` and the ACK, so the ACK can
    only ever say `queued`"). That reading is wrong — the deferred branch awaits
    `asyncio.wait_for(asyncio.shield(task), _grace)` before building the ACK, so the
    task runs inside the window and does reach the assignment — but the assignment
    had NO row that could red it, which is the defect the finding names either way.
    This row is that row, and it is deterministic (no sleep, no deadline).

    Two mutants, both red here:
      * delete `view["phase"] = "extracting"` → the last assertion reds (the view
        stays `queued`, so an ACK built from it lags a record that says
        `extracting`, i.e. the property is gone);
      * move the assignment ABOVE the record write → the `seen` assertion reds (the
        view would claim a phase the record does not carry yet — the one thing the
        ACK must never do).
    """
    _force_ack(monkeypatch)
    session_id = "s-14232-phase-view"
    r = client.post("/v1/sessions", json={
        "conversation": _CONV, "session_id": session_id})
    assert r.status_code == 200, r.text
    _drain_tails()  # the deferred tail finished: the record's phase is cleared

    view = {"phase": "queued", "armed": True}
    seen: list[tuple[str, dict]] = []
    real_mark = ha._mark_capture_extraction_phase

    async def _recording_mark(proj, sid, phase, now):
        # The view AS IT IS DURING the record write.
        seen.append((phase, dict(view)))
        return await real_mark(proj, sid, phase, now)

    monkeypatch.setattr(ha, "_mark_capture_extraction_phase", _recording_mark)
    now = datetime.now(UTC).isoformat()

    asyncio.run(ha._capture_extraction_started(_proj(), session_id, view, now))

    assert seen == [("extracting", {"phase": "queued", "armed": True})], (
        "the record write must happen BEFORE the view is updated, and it must say "
        f"`extracting`: {seen}")
    assert view["phase"] == "extracting", (
        "the view the deferred ACK is built from must name the phase the record now "
        f"carries — otherwise the ACK lags a record it is supposed to describe: {view}")
    row = _session_row(session_id)
    assert row and row[0][3] == "extracting", (
        f"the RECORD must carry the phase the view names: {row}")

    # The armed guard: an inline (never-deferred) lane writes no phase at all.
    disarmed = {"phase": "queued", "armed": False}
    before = len(seen)
    asyncio.run(ha._capture_extraction_started(_proj(), session_id, disarmed, now))
    assert len(seen) == before and disarmed["phase"] == "queued", (
        "an unarmed view must not write a phase and must not claim one: "
        f"{seen} {disarmed}")


# ── the acceptance artifact's verdict (review round 5, F1) ────────────────

def _drain_tool():
    """The acceptance tool, imported as a module (it is a script, not a package)."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "tools" / "acceptance_14232_drain.py"
    spec = importlib.util.spec_from_file_location("_acceptance_14232_drain", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("host_independent_ok", [True, False])
@pytest.mark.parametrize("ack_graded,ok_ack", [
    (True, True),    # graded, met            -> PASS
    (True, False),   # graded, BREACH         -> FAIL  (the fail-open this pins)
    (False, True),   # not graded, p95 met    -> INCONCLUSIVE (never PASS)
    (False, False),  # not graded, p95 breach -> INCONCLUSIVE (no verdict on it)
])
def test_the_acceptance_verdict_cannot_fail_open(host_independent_ok, ack_graded,
                                                 ok_ack):
    """`_verdict` — the graded p95 must be able to FAIL the run, and to exit 1.

    The two arms are symmetric holes and the tool had only closed one of them:

    * a criterion this run DECLINED to grade may not produce `PASS` (fixed
      earlier: `NOT GRADED` beside `VERDICT: PASS` at load 21.29);
    * a criterion that RAN and FAILED may not produce `INCONCLUSIVE`/exit 0 —
      which printed `[FAIL] ack p95` and exited 0 in the same run, leaving the
      issue's headline criterion unfalsifiable in the artifact the PR body cites
      as the issue's done-when (review round 5, F1).

    Mutant: restore `"FAIL" if not all((ok_refusal, ok_terminal, ok_extract))` with
    no p95 arm → the `(True, False)` case returns INCONCLUSIVE and reds.
    """
    drain = _drain_tool()
    verdict = drain._verdict(ok_refusal=host_independent_ok,
                             ok_terminal=host_independent_ok,
                             ok_extract=host_independent_ok,
                             ack_graded=ack_graded, ok_ack=ok_ack)

    if not host_independent_ok:
        expected = "FAIL"
        why = ("a host-independent criterion failed — that is a FAIL on every "
               "host, whatever the p95 said")
    elif ack_graded:
        expected = "PASS" if ok_ack else "FAIL"
        why = ("a GRADED p95 that breached must FAIL the run, not be excused as "
               "INCONCLUSIVE")
    else:
        expected = "INCONCLUSIVE"
        why = "a p95 this host declined to grade is not a pass"
    assert verdict == expected, f"{why}: expected {expected}, got {verdict}"

    # …and the EXIT CODE follows it: INCONCLUSIVE exits 0 (the change is not what
    # failed), FAIL exits 1. A run that printed FAIL and exited 0 is the fail-open
    # this whole row exists to pin, so the mapping is asserted, not restated.
    assert drain._exit_code(verdict) == (1 if expected == "FAIL" else 0), (
        f"verdict {verdict} must exit {1 if expected == 'FAIL' else 0}, not "
        f"{drain._exit_code(verdict)} — a FAIL that exits 0 is the fail-open")


def test_the_deployment_transport_MEASURES_extraction_from_the_detail_body():
    """A successful detail read IS criterion 4's measurement (review round 8, P1).

    The arm hard-coded `{"measured": False}` with "no graph read over HTTP" as the
    reason, which made `ok_extract` unsatisfiable BY CONSTRUCTION: a deployment run
    could never PASS, and a reviewer measured exactly that — a stub deployment that
    answered every criterion correctly still printed `[FAIL] extraction demonstrably
    ran` and exited 1, while the PR body and `_verdict`'s docstring both claimed the
    deployment arm grades the same criteria.

    What the HTTP surface cannot answer is entities/edges, and those stay
    unmeasured — governed by `--require-entities`. The point count IS in the body,
    under the keys the projection actually serves (`extracted`, and the
    `extracted_points` list it counts) — not under `points`, which is the key the
    first cut of this fix read and why it changed nothing.
    """
    drain = _drain_tool()

    from tortoise.session_projection import SESSION_DETAIL_FIELDS

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            # ⛔ BUILT FROM THE REAL PROJECTION, NOT FROM A FICTION (review round 9,
            # P2). The first cut of this row stubbed `{"points": [...]}` — a key
            # `GET /v1/sessions/{id}` has never served — so the row passed while the
            # deployment arm read an always-empty list and still could not PASS. The
            # body here carries exactly the served keys, so a projection drift (or a
            # reader keying off something the surface does not publish) reds.
            body = {
                "id": "s-1", "created_at": "2026-11-22T00:00:00Z", "turns": 2,
                "extracted": 1, "actor_user_id": None, "harness": "claude-code",
                "actor_display": None, "machine_id": None, "model": "m",
                "extraction": {"status": "ok", "outcome_known": True},
                "client_captured_at": None, "client_captured_at_source": None,
                "capture_lane": "v2", "turn_points": 3,
                "extracted_points": [{"id": "p1"}], "source": "capture",
            }
            assert set(body) == set(SESSION_DETAIL_FIELDS), (
                "this stub must be the SERVED body, or the row measures a fiction: "
                f"{sorted(set(body) ^ set(SESSION_DETAIL_FIELDS))}")
            return body

    class _Transport(drain._HttpTransport):
        def __init__(self):  # no server, no auth — the READ is what is under test
            pass

        def get_session(self, _sid):
            return _Resp()

    ev = _Transport().extraction_evidence("s-1")
    assert ev["measured"] is True, (
        "a 200 detail read must count as MEASURED, or no deployment run can ever "
        f"reach PASS: {ev}")
    assert ev["points"] == 1, ev


def test_run_emits_THE_DECISION_POINTS_verdict_and_exit(monkeypatch, capsys,
                                                       tmp_path):
    """The routing pin: `run()` must not restate the conjunction (review round 8, P2).

    The row above pins `_verdict`/`_exit_code` in isolation, which is exactly the
    hole a reviewer measured: reverting the CALL SITE to the pre-fix expression
    (`"FAIL" if not all(...) else "PASS" if (ack_graded and ok_ack) else
    "INCONCLUSIVE"`) left all 8 parametrisations green, because nothing observed
    whether `run()` still goes through the one decision point. A later edit could
    therefore reinstate the `[FAIL] ack p95` + exit-0 fail-open with a green tree.

    Driven end to end with a stub transport and a SENTINEL verdict: whatever
    `_verdict` returns must be what the report carries and what the exit code is
    computed from — so a call site that decides for itself reds here.
    """
    import json as _json

    drain = _drain_tool()

    class _Resp:
        def __init__(self, code, body):
            self.status_code = code
            self._body = body

        def json(self):
            return self._body

    class _Stub:
        """Every criterion met, so the run reaches the verdict with no host in it."""

        label = "stub"

        def __init__(self, *_a, **_kw):
            pass

        def authenticate(self, _org):
            pass

        def close(self):
            pass

        def post_capture(self, _cap):
            return _Resp(200, {"status": "ok", "extracted": 1,
                               "points": [{"id": "p1"}], "extraction": None})

        def get_session(self, _sid):
            return _Resp(200, {"points": [{"id": "p1"}],
                               "extraction": {"status": "ok",
                                              "outcome_known": True}})

        def extraction_evidence(self, _sid):
            return {"measured": True, "points": 1}

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    # The corpus comes from the spool when it exists (`~/.tortoise/capture-spool`
    # on a capture box — present but empty on this one, which would end the run at
    # "nothing to drain" before any verdict). Pin the synthetic corpus instead so
    # this row is about the VERDICT ROUTING and nothing else.
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drain, "_InProcessTransport", _Stub)
    monkeypatch.setattr(drain, "_verdict", lambda **_kw: "SENTINEL-FROM-_verdict")
    exits: list = []
    monkeypatch.setattr(drain, "_exit_code",
                        lambda v: (exits.append(v), 0)[1])

    rc = drain.main(["--json", "--n", "2"])
    assert rc == 0
    report = _json.loads(capsys.readouterr().out)
    assert report["verdict"] == "SENTINEL-FROM-_verdict", (
        "run() must emit the verdict `_verdict` returned, not one it computed "
        f"itself: {report['verdict']}")
    assert exits == ["SENTINEL-FROM-_verdict"], (
        "the exit code must be derived from that same verdict: "
        f"{exits}")


def test_the_transport_error_classifier_is_ISINSTANCE_not_a_name_match():
    """`_is_transport_error` — the classification arm of note 1 (7074468018).

    The pre-fix predicate was
    `type(exc).__name__ == "HTTPError" or "httpx" in type(exc).__module__`. The
    NAME arm is false for every CONCRETE httpx error (`ConnectError`,
    `ReadTimeout`, …), and the module arm only matches when the exception's own
    module string happens to contain `httpx` — so a transport error raised from a
    vendored/wrapped module was classified as a criterion FAILURE and re-raised as
    a verdict about the change.

    The row pins a real `httpx.HTTPError` subclass whose `__module__` is not
    `httpx`, which the module substring cannot rescue, plus the negative that
    keeps the guard from swallowing genuine failures.

    Mutant: restore the name/substring predicate → `_Vendored` classifies False
    and this row reds.
    """
    import httpx

    drain = _drain_tool()

    class _Vendored(httpx.HTTPError):
        """A concrete httpx error re-exported from a vendor module."""

    _Vendored.__module__ = "vendor.transport"

    assert drain._is_transport_error(_Vendored("connection refused")) is True, (
        "a subclass of httpx.HTTPError IS a transport error whatever module it "
        "is raised from")
    assert drain._is_transport_error(httpx.ConnectError("refused")) is True
    assert drain._is_transport_error(ValueError("a criterion bug")) is False, (
        "the guard must not swallow a genuine failure — it would turn a broken "
        "criterion into exit 2")


@pytest.mark.parametrize("boom", ["post_capture", "get_session", "extraction_evidence"])
def test_a_transport_failure_on_ANY_NETWORK_CALL_is_UNREACHABLE_not_a_verdict(
        boom, monkeypatch, capsys, tmp_path):
    """The guard must cover ALL THREE network calls, not one (note 1, 7074468018).

    Only `post_capture` was wrapped, so a `ConnectError` on a detail read — the
    criterion-3 terminal poll, or the one inside `extraction_evidence` for
    criterion 4 — surfaced as a traceback and exited 1. This tool's own contract
    reads exit 1 as "a criterion RAN and FAILED", so one API blip on a deployment
    run was reported as a FAIL of the CHANGE, which is the exact class the
    `post_capture` guard exists to stop.

    ⛔ ALL THREE arms are parametrised, including `post_capture` (review round M,
    P2): the docstring claimed all three report through `_unreachable` while only
    the two detail reads had a row, so deleting the ACK's guard reddened NOTHING.
    The `closes` assertion is the witness for `_close()` on the early path (round
    M, P3): without it, dropping that close reddened nothing either.

    Mutants, one per arm: delete the `try/except` around that call → the arm reds
    with the propagating `ConnectError` instead of rc 2; drop `_close()` from
    `_unreachable` → `closes == 0` reds.
    """
    drain = _drain_tool()

    class _Resp:
        def __init__(self, code, body):
            self.status_code = code
            self._body = body

        def json(self):
            return self._body

    def _boom(_arg=None):
        import httpx

        raise httpx.ConnectError("connection refused")

    class _Stub:
        """Every criterion would be met; the NETWORK is what fails here."""

        label = "stub"
        closes = 0

        def __init__(self, *_a, **_kw):
            pass

        def authenticate(self, _org):
            pass

        def close(self):
            type(self).closes += 1

        def post_capture(self, _cap):
            if boom == "post_capture":
                _boom()
            return _Resp(200, {"status": "ok", "extracted": 1,
                               "extraction": None})

        def get_session(self, sid):
            if boom == "get_session":
                _boom(sid)
            # TERMINAL on the first poll, so the other arms reach criterion 4.
            return _Resp(200, {"extracted": 1,
                               "extraction": {"status": "ok",
                                              "outcome_known": True}})

        def extraction_evidence(self, sid):
            if boom == "extraction_evidence":
                _boom(sid)
            return {"measured": True, "extracted": 1}

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drain, "_InProcessTransport", _Stub)

    rc = drain.main(["--n", "1"])
    err = capsys.readouterr().err
    assert rc == 2, (
        f"a network failure on {boom} is a USAGE error (exit 2), not a FAIL of "
        f"the change (exit 1): rc={rc} stderr={err!r}")
    assert "unreachable" in err and "ConnectError" in err, (
        f"the failure must name the deployment as unreachable: {err!r}")
    assert "Traceback" not in err, f"a usage error must not print a traceback: {err!r}"
    assert _Stub.closes == 1, (
        "the early `return 2` must release the rehearsal's graph handle — "
        f"closes={_Stub.closes}")


def test_the_normal_path_releases_the_transport_too(monkeypatch, capsys, tmp_path):
    """The tail's close is load-bearing, and a full run must reach PASS/exit 0.

    Round M (P3) measured that dropping the tail `_close` reddened nothing at all —
    a leaked graph handle was unpinned in both directions.

    ⛔ AND THE EXIT CODE IS PINNED, not waved through (review round N, P2). This
    row used to accept `rc in (0, 1)`, which cannot tell the normal path from a
    FAIL — and at the head it was written on its own scenario exited 1, because the
    stub returned the SERVED key (`extracted`) where the tool grades its internal
    one (`points`). `--max-ack-p95 3600` keeps the absolute p95 criterion out of it:
    the point of the row is the exit path, not this box's scheduler. Mutant: make
    `ok_points` unconditionally False (the round-8 "a deployment can never PASS"
    regression) → this row reds, and before it NOTHING pinned an end-to-end PASS.
    """
    drain = _drain_tool()

    class _Resp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class _Stub:
        label = "stub"
        closes = 0

        def __init__(self, *_a, **_kw):
            pass

        def authenticate(self, _org):
            pass

        def close(self):
            type(self).closes += 1

        def post_capture(self, _cap):
            return _Resp({"status": "ok", "extracted": 1, "extraction": None})

        def get_session(self, _sid):
            return _Resp({"extracted": 1,
                          "extraction": {"status": "ok", "outcome_known": True}})

        def extraction_evidence(self, sid):
            # `points` is the TOOL's key and the one `ok_points` grades; the
            # served HTTP surface publishes `extracted`/`extracted_points`, and
            # `_HttpTransport.extraction_evidence` maps them onto `points`.
            return {"measured": True, "points": 1, "session_id": sid}

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # A LOAD THAT GRADES THE ABSOLUTE CRITERION. Without this the row's verdict is
    # `INCONCLUSIVE` on any box whose load exceeds its cpu count — i.e. this row
    # would assert PASS on an idle host and fail on a loaded one, which is the
    # #5049 rule-1 class the tool itself refuses to grade.
    monkeypatch.setattr(drain.os, "getloadavg", lambda: (0.1, 0.1, 0.1))
    monkeypatch.setattr(drain, "_InProcessTransport", _Stub)

    rc = drain.main(["--n", "1", "--max-ack-p95", "3600"])
    out = capsys.readouterr().out
    assert rc == 0, (
        f"a run whose four criteria all pass must exit 0 (PASS), not {rc}:\n{out}")
    assert "VERDICT: PASS" in out, out
    assert _Stub.closes == 1, f"the tail must close the transport: closes={_Stub.closes}"


def test_a_raising_CLOSE_cannot_replace_the_exit_code(monkeypatch, capsys, tmp_path):
    """A `close()` that throws must not turn a finished run into a traceback.

    Round N (P3): the `contextlib.suppress` in `_close` was asserted in prose and
    covered by NO row — deleting it left every row green. Measured with it removed:
    this scenario propagates `RuntimeError` and the run's PASS/exit-0 is replaced by
    a traceback at 1 (and on the early `unreachable` path, a clean exit 2 by the
    same traceback).
    """
    drain = _drain_tool()

    class _Resp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class _Stub:
        label = "stub"
        closes = 0

        def __init__(self, *_a, **_kw):
            pass

        def authenticate(self, _org):
            pass

        def close(self):
            type(self).closes += 1
            raise RuntimeError("close blew up")

        def post_capture(self, _cap):
            return _Resp({"status": "ok", "extracted": 1, "extraction": None})

        def get_session(self, _sid):
            return _Resp({"extracted": 1,
                          "extraction": {"status": "ok", "outcome_known": True}})

        def extraction_evidence(self, sid):
            return {"measured": True, "points": 1, "session_id": sid}

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drain, "_InProcessTransport", _Stub)

    rc = drain.main(["--n", "1", "--max-ack-p95", "3600"])
    captured = capsys.readouterr()
    assert _Stub.closes == 1, _Stub.closes
    assert rc == 0, (
        "a raising close must not change the verdict it was called to preserve: "
        f"rc={rc} out={captured.out!r} err={captured.err!r}")
    assert "close blew up" not in captured.err, captured.err


def test_an_UNREADABLE_response_is_routed_by_the_RUN(monkeypatch, capsys, tmp_path):
    """`_wire_failure`'s unreadable branch is load-bearing in `run()`.

    Round N (P2): deleting the two lines that route `_is_unreadable_body` to exit 2
    left every row green, because the type and the two decode sites are pinned but
    the GLUE between them is not. With it gone, the deployment path regresses to
    exactly the defect the branch closes: `_HttpTransport.extraction_evidence`
    raises `_UnreadableResponse`, `_wire_failure` returns None, and the exception
    propagates — a shell exit 1, which this tool's contract reads as "a criterion
    ran and failed".
    """
    drain = _drain_tool()

    class _Resp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class _Stub:
        label = "stub"
        closes = 0

        def __init__(self, *_a, **_kw):
            pass

        def authenticate(self, _org):
            pass

        def close(self):
            type(self).closes += 1

        def post_capture(self, _cap):
            return _Resp({"status": "ok", "extracted": 1, "extraction": None})

        def get_session(self, _sid):
            return _Resp({"extracted": 1,
                          "extraction": {"status": "ok", "outcome_known": True}})

        def extraction_evidence(self, sid):
            # The shape `_HttpTransport.extraction_evidence` raises when the detail
            # body will not decode — raised HERE so the routing, not the decode, is
            # what this row exercises.
            raise drain._UnreadableResponse("ValueError: garbled")

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drain, "_InProcessTransport", _Stub)

    rc = drain.main(["--n", "1", "--max-ack-p95", "3600"])
    err = capsys.readouterr().err
    assert rc == 2, (
        "an undecodable response is a broken wire (exit 2), never a verdict: "
        f"rc={rc} err={err!r}")
    assert "unreadable response" in err, err
    assert _Stub.closes == 1, _Stub.closes


def test_a_GARBLED_ack_body_is_reported_as_NOT_MEASURABLE(
        monkeypatch, capsys, tmp_path):
    """A garbled ACK body must not be reported as "nothing deferred".

    Round N (P3): the tolerant decode is deliberate (a garbled ack must not fail
    the run), but the report then said "extraction finished inside the budget on
    every session" about a body it never read — a measurement claim with nothing
    behind it, in the one line that describes the split itself.
    """
    drain = _drain_tool()

    class _Garbled:
        status_code = 200

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    class _Resp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class _Stub:
        label = "stub"
        closes = 0

        def __init__(self, *_a, **_kw):
            pass

        def authenticate(self, _org):
            pass

        def close(self):
            type(self).closes += 1

        def post_capture(self, _cap):
            return _Garbled()

        def get_session(self, _sid):
            return _Resp({"extracted": 1,
                          "extraction": {"status": "ok", "outcome_known": True}})

        def extraction_evidence(self, sid):
            return {"measured": True, "points": 1, "session_id": sid}

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drain, "_InProcessTransport", _Stub)

    drain.main(["--n", "1", "--max-ack-p95", "3600"])
    out = capsys.readouterr().out
    assert "NOT MEASURABLE" in out, out
    assert "extraction finished inside the budget" not in out, (
        f"the run must not claim a deferral measurement it could not make: {out}")


def test_a_NON_transport_exception_is_not_laundered(monkeypatch, capsys, tmp_path):
    """A criterion BUG must propagate (never exit 2) — the no-fail-open direction.

    Round M (P3) measured that nothing drove propagation end to end: the classifier
    row only called `_is_transport_error` directly, while the commit message said
    the negative was pinned. It also pins the honest scope of `_close()`'s
    docstring: a non-transport exception propagates WITHOUT a close by design, so
    this row asserts that too rather than claiming "every exit path".
    """
    drain = _drain_tool()

    class _Stub:
        label = "stub"
        closes = 0

        def __init__(self, *_a, **_kw):
            pass

        def authenticate(self, _org):
            pass

        def close(self):
            type(self).closes += 1

        def post_capture(self, _cap):
            raise ValueError("a criterion bug, not a wire")

        def get_session(self, _sid):  # pragma: no cover — never reached
            raise AssertionError("unreachable")

        def extraction_evidence(self, sid):  # pragma: no cover — never reached
            raise AssertionError("unreachable")

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drain, "_InProcessTransport", _Stub)

    with pytest.raises(ValueError, match="a criterion bug"):
        drain.main(["--n", "1"])
    capsys.readouterr()
    assert _Stub.closes == 0, (
        "a non-transport exception propagates unclosed by design — `_close`'s "
        "docstring must not claim every exit path")


def test_an_UNDECODABLE_200_is_a_broken_wire_not_a_verdict(monkeypatch, capsys, tmp_path):
    """A 200 whose body will not decode must exit 2, not 1 with a traceback.

    Round M (P3): `r.json()` sits one line below the transport guard and raises
    `ValueError`, which `_is_transport_error` correctly refuses — so a garbled
    response exited 1, i.e. "a criterion RAN and FAILED", the same laundering the
    guard exists to stop. The ACK read stays tolerant (it degrades to `body=None`,
    pre-existing); the two DETAIL reads do not, because there a missing body is a
    verdict about extraction.
    """
    drain = _drain_tool()

    class _Resp:
        status_code = 200

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    class _Stub:
        label = "stub"
        closes = 0

        def __init__(self, *_a, **_kw):
            pass

        def authenticate(self, _org):
            pass

        def close(self):
            type(self).closes += 1

        def post_capture(self, _cap):
            return _ok_resp()

        def get_session(self, _sid):
            return _Resp()

        def extraction_evidence(self, sid):  # pragma: no cover — never reached
            raise AssertionError("unreachable")

    class _Ok:
        status_code = 200

        def json(self):
            return {"status": "ok", "extracted": 1, "extraction": None}

    def _ok_resp():
        return _Ok()

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drain, "_InProcessTransport", _Stub)

    rc = drain.main(["--n", "1"])
    err = capsys.readouterr().err
    assert rc == 2, (
        f"a garbled 200 is a broken wire (exit 2), not a FAIL of the change: "
        f"rc={rc} stderr={err!r}")
    assert "unreadable response" in err and "ValueError" in err, err
    assert "Traceback" not in err, err


def test_an_UNUSABLE_base_url_is_a_usage_error(monkeypatch, capsys, tmp_path):
    """`httpx.InvalidURL` is not an `HTTPError`, so it must be caught by the build.

    Round M (P3) measured `--base-url 'http://[::1'` raising from
    `_HttpTransport.__init__`, outside every guard → a traceback at exit 1 where
    the contract says 2. `HOME` is redirected so the corpus is the SYNTHETIC one:
    the default spool path exists on a real box and would end the run at the
    corpus check before the URL is ever built.
    """
    drain = _drain_tool()
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = drain.main(["--n", "1", "--base-url", "http://[::1",
                     "--api-key", "k"])
    err = capsys.readouterr().err
    assert rc == 2, f"an unusable --base-url is a USAGE error: rc={rc} stderr={err!r}"
    assert "is not usable" in err, err
    assert "Traceback" not in err, err


def test_REQUIRE_ENTITIES_over_HTTP_is_a_usage_error(monkeypatch, capsys,
                                                     tmp_path):
    """A criterion the HTTP surface cannot measure is REFUSED, not failed.

    Review round R (P2): `--require-entities --base-url` could NEVER PASS. The
    detail body serves a point count and no entity/edge count, so `ok_entities`
    was False by construction — while argparse described the flag as "pass this
    against a deployment with a real extractor". A perfect deployment (200 ack
    inside the window, terminal on its own, one extracted point) printed
    `[FAIL] extraction demonstrably ran` and exited 1: a criterion FAIL blamed on
    the change for a measurement the instrument cannot take.

    Mutant: delete the refusal → the run proceeds to the network and reports
    `unreachable`, so this row reds on the message it requires.
    """
    drain = _drain_tool()
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = drain.main(["--n", "1", "--base-url", "http://127.0.0.1:1",
                     "--api-key", "k", "--require-entities"])
    err = capsys.readouterr().err
    assert rc == 2, (
        f"an unmeasurable criterion is a USAGE error, never a verdict: rc={rc}\n{err}")
    assert "cannot be graded over HTTP" in err, err
    assert "unreachable" not in err, (
        "the refusal must come BEFORE any network call, or a usage error is "
        f"reported as a transport failure: {err}")
    assert "Traceback" not in err, err



def test_a_garbled_HTTP_detail_body_is_an_UNREADABLE_RESPONSE():
    """The HTTP transport's own decode site raises the DEDICATED type.

    `_HttpTransport.extraction_evidence` decodes the body INSIDE the transport, so
    `run()` cannot put a `try` around that `.json()` — it sees the exception the
    method raises. A bare `ValueError` is indistinguishable from a criterion bug
    there (measured: classifying every `ValueError` as a wire failure made a
    `ValueError` from a transport method exit 2 — the laundering this tool
    refuses). The conversion is therefore the transport's, and this row pins it.

    Mutant: delete the `except ValueError → _UnreadableResponse` conversion → this
    row reds with the raw `ValueError`.
    """
    drain = _drain_tool()

    class _Garbled:
        status_code = 200

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    class _Transport(drain._HttpTransport):
        def __init__(self):  # no client, no network
            pass

        def get_session(self, _sid):
            return _Garbled()

    with pytest.raises(drain._UnreadableResponse, match="ValueError"):
        _Transport().extraction_evidence("s-1")
    assert drain._is_unreadable_body(drain._UnreadableResponse("x")) is True
    assert drain._is_unreadable_body(ValueError("a criterion bug")) is False, (
        "the classifier must stay narrow: only the decode sites raise the "
        "dedicated type")


# ── The four criterion INPUTS, each with a negative witness ───────────────────
# Round O (P2) measured that only `ok_points` was pinned as an input to `_verdict`:
# forcing `ok_refusal`, `ok_terminal`, `ok_ack` or `oversubscribed` left the WHOLE
# FILE green, so a regression in any of them would ship a false PASS. These rows
# drive `run()` end to end and assert the VERDICT and the exit code each input
# produces — a False criterion FAILs (1), and an ungraded ack criterion is
# INCONCLUSIVE at 0, never PASS.

def _criterion_drain(monkeypatch, tmp_path, *, ack=None, session=None,
                     evidence=None, load=0.1):
    """Drive `run()` with the four criteria satisfied unless overridden."""
    drain = _drain_tool()

    class _Resp:
        def __init__(self, code, body):
            self.status_code = code
            self._body = body
            self.text = str(body)

        def json(self):
            return self._body

    ok_ack = ack or (lambda _cap: _Resp(200, {"status": "ok", "extracted": 1,
                                              "extraction": None}))
    ok_session = session or (lambda _sid: _Resp(
        200, {"extracted": 1,
              "extraction": {"status": "ok", "outcome_known": True}}))
    ok_evidence = evidence or (lambda sid: {"measured": True, "points": 1,
                                            "session_id": sid})

    class _Stub:
        label = "stub"
        closes = 0

        def __init__(self, *_a, **_kw):
            pass

        def authenticate(self, _org):
            pass

        def close(self):
            type(self).closes += 1

        def post_capture(self, cap):
            return ok_ack(cap)

        def get_session(self, sid):
            return ok_session(sid)

        def extraction_evidence(self, sid):
            return ok_evidence(sid)

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drain.os, "getloadavg", lambda: (load, load, load))
    monkeypatch.setattr(drain, "_InProcessTransport", _Stub)
    return drain


def test_a_REFUSAL_fails_the_run(monkeypatch, capsys, tmp_path):
    """`ok_refusal` is an INPUT to the DECISION, not merely to a printed line.

    ⛔ A MIXED POPULATION, and that is the whole point (review round P, P2): with
    `--n 1` and a 504, `accepted == 0` makes `ok_terminal` and `ok_extract` False
    too, so the run FAILs for three reasons and deleting `ok_refusal` from
    `_verdict`'s conjunction changed NOTHING (measured: 27/27 rows green, and a
    mixed population then printed `[FAIL] zero refusals` beside `VERDICT: PASS` at
    rc 0 — round 5's F1 shape). Two sessions, one clean and one refused, leave
    `ok_terminal` and `ok_extract` TRUE so the refusal is the ONLY False input.

    Mutants: `ok_refusal = True` → this row reds; dropping `ok_refusal` from
    `_verdict` → this row reds (PASS at rc 0 instead of FAIL at 1).
    """
    drain = _criterion_drain(
        monkeypatch, tmp_path,
        ack=lambda cap: (_Resp504() if cap["session_id"].endswith("-001")
                         else _Resp200({"status": "ok", "extracted": 1,
                                        "extraction": None})))
    rc = drain.main(["--n", "2", "--max-ack-p95", "3600"])
    out = capsys.readouterr().out
    assert rc == 1, f"a refusal is a criterion FAIL (exit 1): rc={rc}\n{out}"
    assert "[FAIL] zero refusals" in out, out
    assert "VERDICT: FAIL" in out, out
    assert "[FAIL] terminal on its own" not in out, (
        "the mixed population must leave the OTHER criteria TRUE, or this row "
        f"cannot witness `ok_refusal` as the decision input: {out}")
    assert "[FAIL] extraction demonstrably ran" not in out, out


class _Resp504:
    status_code = 504
    text = '{"stage": "capture_gates"}'

    def json(self):
        return {"stage": "capture_gates", "stages_ms": {"capture_gates": 900}}


def test_a_STUCK_session_fails_the_run(monkeypatch, capsys, tmp_path):
    """`ok_terminal` is an INPUT: a session that never terminates must FAIL.

    Mutant: `ok_terminal = True` → this row reds (GREEN file-wide before, round O).
    """
    drain = _criterion_drain(
        monkeypatch, tmp_path,
        session=lambda sid: (
            _Resp200({"extracted": 1,
                      "extraction": {"status": "ok", "outcome_known": True}})
            if sid.endswith("-000") else
            _Resp200({"extracted": 1,
                      "extraction": {"outcome_known": False,
                                     "phase": "extracting"}})))
    # ⛔ TWO SESSIONS (review round P, P2): with `--n 1` and a stuck session there
    # are no terminals at all, so `ok_extract` is False too and the run FAILs for
    # two reasons — deleting `ok_terminal` from `_verdict` left every row green.
    # One terminal + one stuck leaves the extraction criterion TRUE, so
    # `ok_terminal` is the only False input.
    rc = drain.main(["--n", "2", "--max-ack-p95", "3600",
                     "--terminal-timeout", "0.2"])
    out = capsys.readouterr().out
    assert rc == 1, f"a stuck session is a criterion FAIL (exit 1): rc={rc}\n{out}"
    assert "[FAIL] terminal on its own" in out, out
    assert "VERDICT: FAIL" in out, out
    # ⛔ THE OTHER CRITERIA ARE ASSERTED, not assumed (review round Q, P3): this
    # row's witness for `ok_terminal` rests on `ok_refusal`/`ok_extract` being
    # TRUE, and without these two lines a drifting evidence stub hid the mutant
    # entirely (measured: `_verdict(ok_terminal=True)` + `points: 0` stayed GREEN).
    assert "[FAIL] zero refusals" not in out, out
    assert "[FAIL] extraction demonstrably ran" not in out, out


class _Resp200:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def test_a_GRADED_ack_BREACH_fails_the_run(monkeypatch, capsys, tmp_path):
    """`ok_ack` is an INPUT, and a breach must FAIL — never PASS, never exit 0.

    This is round 5's F1 in its living form (`[FAIL] ack p95` beside `VERDICT:
    PASS` at exit 0). Mutant: `ok_ack = True` → this row reds (GREEN file-wide
    before, round O).
    """
    drain = _criterion_drain(monkeypatch, tmp_path)
    rc = drain.main(["--n", "1", "--max-ack-p95", "0.000001"])
    out = capsys.readouterr().out
    assert rc == 1, f"a graded breach is a FAIL (exit 1): rc={rc}\n{out}"
    assert "[FAIL] ack p95" in out, out
    assert "VERDICT: PASS" not in out, out


def test_the_p95_is_the_NEAREST_RANK_not_a_ROUNDED_one():
    """`_percentile` must not flatter the issue's headline criterion.

    Review round R (P3): the code used `round()` where nearest-rank is `ceil`, so
    the reported p95 was one rank LOW for most `n != 50` (measured: 95 of
    `n = 1..200` differ) — and low is the LENIENT direction. With `n=11`, ten
    1 ms acks and one 5000 ms ack, the tool reported a p95 of **1 ms**, so
    `ok_ack` was True and the run printed PASS with a 5 s ack in its own sample.

    Mutant: restore `round(pct / 100 * n)` → this row reds (1 ms, not 5000).
    """
    drain = _drain_tool()
    acks = [0.001] * 10 + [5.0]
    assert drain._percentile(acks, 95) == 5.0, (
        "the p95 of 11 samples is the LARGEST one; a rounded rank reports a "
        f"smaller rank instead: {drain._percentile(acks, 95)}")
    # The same must hold at EVERY n, not just the one shape that was measured.
    for n in range(1, 201):
        vals = [float(i) for i in range(n)]
        assert drain._percentile(vals, 95) == vals[max(0, math.ceil(0.95 * n) - 1)], (
            f"nearest-rank mismatch at n={n}")
    assert drain._percentile([], 95) == 0.0


def test_an_OVERSUBSCRIBED_host_is_INCONCLUSIVE_never_PASS(monkeypatch, capsys,
                                                           tmp_path):
    """`ack_graded` is an INPUT: the absolute criterion is not graded on a busy box.

    #5049 rule 1 — a deadline expiry is evidence about the machine. Mutant:
    `oversubscribed = False` → this row reds (GREEN file-wide before, round O).
    """
    drain = _criterion_drain(monkeypatch, tmp_path, load=999.0)
    rc = drain.main(["--n", "1", "--max-ack-p95", "0.000001"])
    out = capsys.readouterr().out
    assert rc == 0, f"INCONCLUSIVE exits 0 (the change is not what failed): {rc}\n{out}"
    assert "VERDICT: INCONCLUSIVE" in out, out
    assert "VERDICT: PASS" not in out, out
    assert "[FAIL] ack p95" not in out, out


def test_no_extracted_points_fails_the_run(monkeypatch, capsys, tmp_path):
    """`ok_points` is an INPUT too — the round-8 "can never PASS" mirror image."""
    drain = _criterion_drain(
        monkeypatch, tmp_path,
        evidence=lambda sid: {"measured": True, "points": 0, "session_id": sid})
    rc = drain.main(["--n", "1", "--max-ack-p95", "3600"])
    out = capsys.readouterr().out
    assert rc == 1, f"no extracted point is a criterion FAIL (exit 1): rc={rc}\n{out}"
    assert "[FAIL] extraction demonstrably ran" in out, out


def test_the_HTTP_transport_releases_its_client():
    """`_HttpTransport` must own a `close()`, and `_close` must route to it.

    Review round R (P3): the class had NO `close()` at all — `hasattr` was False —
    so the deployment run, the only run that talks to real production, left its
    `httpx.Client` (and its pool) to the garbage collector while the rehearsal's
    graph handle was released explicitly.

    Mutant: delete `_HttpTransport.close()` → this row reds on the first assertion.
    """
    drain = _drain_tool()
    assert hasattr(drain._HttpTransport, "close"), (
        "the deployment transport must be releasable, not left to the collector")

    transport = drain._HttpTransport("http://127.0.0.1:1", "k")
    assert transport._client.is_closed is False
    drain._close(transport)
    assert transport._client.is_closed is True, (
        "`_close()` must release the HTTP client, not only the rehearsal handle")


def test_the_rehearsal_ctor_restores_the_db_path_pin_when_it_fails(monkeypatch):
    """The REAL constructor must undo its environment pin before it raises.

    Review round R (P3): `TORTOISE_DB_PATH` was set BEFORE the one fallible call
    and nothing restored it, so a `TestClient` that raised left the process
    pointing at a temp path that was then deleted — every later SDK call in the
    process read a dead database (measured: the env still held
    `/tmp/acc14232-…/acc.db` afterwards).

    Mutant: drop the `try/except BaseException` around the pin + client build →
    the pin survives and this row reds.
    """
    drain = _drain_tool()

    class _BadClient:
        def __init__(self, *_a, **_kw):
            raise RuntimeError("TestClient blew up")

    monkeypatch.delenv("TORTOISE_DB_PATH", raising=False)
    monkeypatch.setattr("fastapi.testclient.TestClient", _BadClient)
    with pytest.raises(RuntimeError, match="blew up"):
        drain._InProcessTransport("acc14232")
    assert "TORTOISE_DB_PATH" not in os.environ, (
        "a failed construction must not leave the environment pointing at a temp "
        f"path it already deleted: {os.environ.get('TORTOISE_DB_PATH')}")


def test_a_rehearsal_ctor_failure_is_a_USAGE_error_not_a_verdict(
        monkeypatch, capsys, tmp_path):
    """A rehearsal that cannot start has measured NOTHING, so it exits 2.

    Review round R (P3): the construction was outside every guard, so the failure
    propagated as a traceback at shell exit **1** — this tool's "a criterion RAN
    and FAILED" — reporting the CHANGE as broken for an environment defect.

    Mutant: drop the `run()` guard → rc becomes 1 via the traceback.
    """
    drain = _drain_tool()

    class _Exploding:
        def __init__(self, *_a, **_kw):
            raise RuntimeError("no embedded graph on this box")

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drain, "_InProcessTransport", _Exploding)

    rc = drain.main(["--n", "1", "--max-ack-p95", "3600"])
    err = capsys.readouterr().err
    assert rc == 2, (
        f"a rehearsal that cannot start has not measured the change: rc={rc}\n{err}")
    assert "could not start" in err, err
    assert "Traceback" not in err, err


def test_the_JSON_report_carries_the_UNREADABLE_ack_count(monkeypatch, capsys,
                                                          tmp_path):
    """`--json` is the documented acceptance mode, so the honest arm must be in it.

    Round O (P2): `NOT MEASURABLE` lived only in the human block, so the machine
    report still stated `deferred_at_ack: {n: 0, of: 1}` for an ack body it never
    read. The summary now carries `unreadable`, and this row pins it under
    `--json`.
    """
    class _Garbled:
        status_code = 200
        text = "not json"

        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    drain = _criterion_drain(monkeypatch, tmp_path,
                             ack=lambda _cap: _Garbled())
    rc = drain.main(["--n", "1", "--max-ack-p95", "3600", "--json"])
    captured = capsys.readouterr()
    assert rc == 0, rc
    report = json.loads(captured.out)
    assert report["deferred_at_ack"]["unreadable"] == 1, report["deferred_at_ack"]
    assert report["deferred_at_ack"]["n"] == 0
    assert report["deferred_at_ack"]["measurable"] is False, (
        "the machine report must not assert measurability it does not have — "
        f"this key was unpinned (round P, P3): {report['deferred_at_ack']}")
    assert "NOT MEASURABLE" in captured.err, captured.err


def test_a_refusal_ONLY_run_does_not_claim_a_deferral_measurement(monkeypatch,
                                                                  capsys,
                                                                  tmp_path):
    """0 accepted acks is `NOT MEASURABLE`, not "every session finished in budget".

    Round O (P3): the 504 run — the defect this tool exists to detect — printed
    "extraction finished inside the budget on every session" over a population of
    zero.
    """
    drain = _criterion_drain(monkeypatch, tmp_path, ack=lambda _cap: _Resp504())
    drain.main(["--n", "1", "--max-ack-p95", "3600"])
    out = capsys.readouterr().out
    assert "extraction finished inside the budget" not in out, out
    assert "NOT MEASURABLE" in out and "0/0" in out, out


def test_a_READABLE_run_reports_the_deferral_as_MEASURABLE(monkeypatch, capsys,
                                                            tmp_path):
    """The other half of `measurable`: pinned so it cannot be hard-coded True.

    Round P (P3) measured that forcing `"measurable": True` left every row green,
    i.e. the machine report could assert measurability it does not have — the exact
    lie the key exists to prevent. This row pins the readable direction.
    """
    drain = _criterion_drain(monkeypatch, tmp_path)
    rc = drain.main(["--n", "1", "--max-ack-p95", "3600", "--json"])
    captured = capsys.readouterr()
    assert rc == 0, rc
    report = json.loads(captured.out)
    assert report["deferred_at_ack"]["measurable"] is True, report["deferred_at_ack"]
    assert report["deferred_at_ack"]["unreadable"] == 0
    # The grading threshold and the enforcement flag travel with the result: a
    # consumer cannot read `criteria` without them (round P, pre-existing P3).
    assert report["ack_p95_budget_ms"] == 3_600_000.0, report["ack_p95_budget_ms"]
    assert report["require_entities"] is False


def test_an_ACCEPTED_ack_with_NO_phase_counts_as_deferred(monkeypatch, capsys,
                                                          tmp_path):
    """`deferred_at_ack` is a phase OR `status == "accepted"`; the second half
    had no witness.

    Review round R (P3): dropping `ack_status == "accepted"` from the expression
    left 56/56 green, and the head then reported `{n: 0, of: 1}` for an ack whose
    body said `accepted` while `outcomes[0].deferred_at_ack` was True — the
    summary contradicting its own per-ack records.

    Mutant: drop `or ack_status == "accepted"` → `n` becomes 0 and this row reds.
    """
    drain = _criterion_drain(
        monkeypatch, tmp_path,
        ack=lambda _cap: _Resp200({"status": "accepted"}))
    rc = drain.main(["--n", "1", "--max-ack-p95", "3600", "--json"])
    captured = capsys.readouterr()
    assert rc == 0, (rc, captured.err)
    report = json.loads(captured.out)
    assert report["deferred_at_ack"]["n"] == 1, report["deferred_at_ack"]
    assert report["deferred_at_ack"]["of"] == 1, report["deferred_at_ack"]
    assert report["outcomes"][0]["deferred_at_ack"] is True, report["outcomes"]


def test_the_json_report_is_PARSEABLE_when_the_spool_has_junk(monkeypatch, capsys,
                                                              tmp_path):
    """`--json` must emit JSON and nothing else — even on the spool path.

    Round P (pre-existing P2, but in the mode this commit's rows rely on):
    `_spooled_corpus` printed `corpus: skipped N …` to STDOUT before the report, so
    the documented acceptance mode exited 0 with output `json.loads` cannot parse —
    on the one path that reads the real spool. The line now goes to stderr under
    `--json`.
    """
    spool = tmp_path / "spool"
    spool.mkdir()
    (spool / "a.json").write_text(json.dumps({
        "session_id": "acc-spool-0",
        "harness": "pi",
        "conversation": [{"role": "user", "content": "spooled capture"},
                         {"role": "assistant", "content": "noted"}],
    }))
    (spool / "b.json").write_text("{not json at all")

    drain = _criterion_drain(monkeypatch, tmp_path)
    rc = drain.main(["--sessions-dir", str(spool), "--n", "5",
                     "--max-ack-p95", "3600", "--json"])
    captured = capsys.readouterr()
    assert rc == 0, rc
    report = json.loads(captured.out)  # raises if anything else hit stdout
    assert report["n"] == 1, report["n"]
    assert "spooled" in report["corpus"] or "spool" in report["corpus"], report["corpus"]
    assert "skipped 1 unreadable/empty spool entries" in captured.err, captured.err


def test_the_corpus_reader_reads_a_REAL_capture_spool(monkeypatch, capsys,
                                                     tmp_path):
    """The documented PRIMARY corpus path must read what the PRODUCT parks.

    Review round R (P2) measured that it could not: a real spool is
    `entries/<key>.meta.json` + `entries/<key>.turns.jsonl`, and the reader globbed
    `*.json`, matched only the meta (which carries no conversation), and returned
    `[]` — so the CLI exited 2 "the corpus is empty — nothing to drain" against
    three entries written by the product's own `write_spool_entry`. The row that
    claimed to cover this path hand-wrote `spool/a.json` with a `conversation`
    key, a shape no producer writes, which is exactly why the hole was invisible.

    Mutants: restore the `rglob("*.json")`-only scan → `n == 0` and exit 2; read
    the conversation out of the meta instead of the paired log → `n == 0`.
    """
    from tortoise.capture_spool import Snapshot, write_spool_entry

    spool = tmp_path / "spool"
    for i in range(3):
        write_spool_entry(spool, Snapshot(
            session_id=f"acc-real-{i}",
            turns=[{"role": "user", "content": f"capture {i} must ack first"},
                   {"role": "assistant", "content": f"agreed {i}"}],
            source="acceptance-14232", machine_id="m1", harness="claude"))

    drain = _criterion_drain(monkeypatch, tmp_path)
    corpus, skipped = drain._spooled_corpus(spool, 5)
    assert skipped == 0, skipped
    assert [c["session_id"] for c in corpus] == [
        "acc-real-0", "acc-real-1", "acc-real-2"], (
        "spool order is the producer's `created_at`, oldest first: "
        f"{[c['session_id'] for c in corpus]}")
    assert all(len(c["conversation"]) == 2 for c in corpus), corpus
    # `--n` truncates the CORPUS, oldest first — and the reader stops there rather
    # than decoding every transcript in the spool (a spool of thousands of parked
    # captures must not cost thousands of reads to drain fifty).
    assert [c["session_id"] for c in drain._spooled_corpus(spool, 1)[0]] == [
        "acc-real-0"]

    rc = drain.main(["--sessions-dir", str(spool), "--n", "5",
                     "--max-ack-p95", "3600", "--json"])
    captured = capsys.readouterr()
    assert rc == 0, (
        "a real spool must be drainable, not \"the corpus is empty\": "
        f"rc={rc} err={captured.err!r}")
    report = json.loads(captured.out)
    assert report["n"] == 3, (
        f"all three parked captures must be read, got {report['n']}: {report['corpus']}")
    assert report["corpus_skipped"] == 0, report["corpus_skipped"]
    assert "skipped" not in captured.err, captured.err


def test_the_corpus_reader_STOPS_at_n_instead_of_reading_every_transcript(
        monkeypatch, tmp_path):
    """The bounded read is a CLAIM, and it had no witness.

    Review cycle 11 (P3): the mutant "read every transcript, then `out[:limit]`"
    left the whole file green — so the `break` that keeps a spool of thousands of
    parked captures from costing thousands of transcript reads was untested, while
    the docstring asserted it. This row counts the calls instead of trusting the
    `break`'s presence.

    Mutant: drop `if len(out) >= limit: break` and truncate at the end → the reader
    is called 3 times for a corpus of 1, and this row reds.
    """
    from tortoise.capture_spool import Snapshot, write_spool_entry

    spool = tmp_path / "spool"
    for i in range(3):
        write_spool_entry(spool, Snapshot(
            session_id=f"acc-bound-{i}",
            turns=[{"role": "user", "content": f"bounded capture {i}"}],
            source="acceptance-14232", machine_id="m1", harness="claude"))

    drain = _drain_tool()
    import tortoise.capture_spool as _spool_mod

    real = _spool_mod.read_spool_turns
    calls: list[str] = []

    def _counting(root, session_id):
        calls.append(session_id)
        return real(root, session_id)

    monkeypatch.setattr(_spool_mod, "read_spool_turns", _counting)
    corpus, skipped = drain._spooled_corpus(spool, 1)
    assert [c["session_id"] for c in corpus] == ["acc-bound-0"], corpus
    assert skipped == 0, skipped
    assert calls == ["acc-bound-0"], (
        "the reader must stop once the corpus is full — it read "
        f"{len(calls)} transcript(s) for a corpus of 1")


def test_an_UNREADABLE_meta_is_COUNTED_even_when_the_corpus_fills_early(
        monkeypatch, tmp_path):
    """An unreadable meta must not hide behind the `--n` cutoff.

    Review cycle 11 (P3): the docstring claims an unparseable meta "sorts FIRST —
    its instant is unknown — so it can never hide behind the cutoff", and sorting
    it LAST left the whole file green. The existing skip-count row cannot isolate
    this: it uses a corpus that never fills (n=5 over 2 entries), so the bad entry
    is reached whatever the order. Only a SMALL `n` makes the order observable.

    Mutant: sort unreadable metas LAST → `skipped` becomes 0 and this row reds.
    """
    from tortoise.capture_spool import Snapshot, write_spool_entry

    spool = tmp_path / "spool"
    for i in range(3):
        write_spool_entry(spool, Snapshot(
            session_id=f"acc-meta-{i}",
            turns=[{"role": "user", "content": f"capture {i}"}],
            source="acceptance-14232", machine_id="m1", harness="claude"))
    (spool / "entries" / "zbad.meta.json").write_text("{not json")

    drain = _drain_tool()
    corpus, skipped = drain._spooled_corpus(spool, 2)
    assert len(corpus) == 2, corpus
    assert skipped == 1, (
        "an unreadable entry must be counted even when `--n` fills before the walk "
        f"reaches it: skipped={skipped}")


def test_an_unimportable_spool_reader_is_a_usage_error(monkeypatch, capsys,
                                                       tmp_path):
    """The dependency on the PRODUCT's spool reader is a PRECONDITION.

    Review cycle 11 (P3): the new `from tortoise.capture_spool import
    read_spool_turns` was unguarded, so on a tree where that module cannot be
    imported the raw `ImportError` escaped `main()` as a traceback at shell exit 1
    — this tool's "a criterion RAN and FAILED", the same defect class the
    transport arms were repaired for. A tree that cannot import the reader it reads
    a spool with has measured nothing about the deployment.

    Mutant: remove the `except ImportError` arm → rc becomes 1 with a traceback.
    """
    from tortoise.capture_spool import Snapshot, write_spool_entry

    spool = tmp_path / "spool"
    write_spool_entry(spool, Snapshot(
        session_id="acc-imp", turns=[{"role": "user", "content": "hi"}],
        source="acceptance-14232", machine_id="m1", harness="claude"))

    drain = _drain_tool()
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    # `None` in `sys.modules` makes the import raise `ImportError`, and the import
    # happens at CALL time (inside `_real_spool_entries`), which is exactly the
    # window this row needs.
    monkeypatch.setitem(sys.modules, "tortoise.capture_spool", None)

    rc = drain.main(["--sessions-dir", str(spool), "--n", "1",
                     "--max-ack-p95", "3600"])
    err = capsys.readouterr().err
    assert rc == 2, (
        f"an unimportable spool reader is a precondition, not a verdict: rc={rc}\n{err}")
    assert "cannot read the capture spool" in err, err
    assert "Traceback" not in err, err


def test_a_rehearsal_AUTHENTICATE_failure_is_a_USAGE_error_not_a_verdict(
        monkeypatch, capsys, tmp_path):
    """An authenticate failure has measured nothing — exit 2, `SystemExit` included.

    Review cycle 11 (P2): the guard was `except Exception`, but `authenticate`'s own
    seed self-check raises `SystemExit`, a `BaseException` — measured escaping
    `main()` and producing shell exit **1**, this tool's "a criterion RAN and
    FAILED". That branch is the documented seed failure ("measured, three
    attempts"), not hypothetical. Deleting the whole guard also left the module
    green (65 passed), so the fix had no witness either.

    Mutants: narrow the clause back to `Exception` → the `SystemExit` escapes and rc
    is 1; delete the guard → rc is 1 via the traceback.
    """
    drain = _drain_tool()

    class _Resp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class _Stub:
        label = "stub"
        closes = 0

        def __init__(self, *_a, **_kw):
            pass

        def authenticate(self, _org):
            # The tool's OWN seed guard, verbatim.
            raise SystemExit("rehearsal consent seed did not persist: {}")

        def close(self):
            type(self).closes += 1

        def post_capture(self, _cap):
            return _Resp({"status": "ok", "extracted": 1, "extraction": None})

        def get_session(self, _sid):
            return _Resp({"extracted": 1,
                          "extraction": {"status": "ok", "outcome_known": True}})

        def extraction_evidence(self, sid):
            return {"measured": True, "points": 1, "session_id": sid}

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drain, "_InProcessTransport", _Stub)

    rc = drain.main(["--n", "1", "--max-ack-p95", "3600"])
    err = capsys.readouterr().err
    assert rc == 2, (
        f"an authenticate failure has not measured the change: rc={rc}\n{err}")
    assert "could not authenticate" in err, err
    assert "consent seed did not persist" in err, err
    assert _Stub.closes == 1, _Stub.closes


def test_a_NON_STRING_created_at_does_not_crash_the_reader(monkeypatch, tmp_path):
    """A corrupt-TYPED `created_at` must not become a `TypeError` at exit 1.

    Review cycle 12 (P2): the oldest-first sort compared raw values, so ONE meta
    hand-edited to `"created_at": 1735689600` raised
    `TypeError: '<' not supported between instances of 'str' and 'int'` out of
    `run()`, and only `ImportError` is guarded there — a traceback at shell exit 1,
    this tool's "a criterion RAN and FAILED". The PRODUCT already defends this exact
    crash in `capture_spool._age_key` with `str(...)`, recording that a corrupt-typed
    `updated_at` "crashed `session spool`"; the reader of that spool must not be
    weaker than its writer.

    Mutant: drop the `str(...)` coercion → this row reds with the `TypeError`.
    """
    from tortoise.capture_spool import Snapshot, write_spool_entry

    spool = tmp_path / "spool"
    for i in range(2):
        write_spool_entry(spool, Snapshot(
            session_id=f"acc-type-{i}",
            turns=[{"role": "user", "content": f"capture {i}"}],
            source="acceptance-14232", machine_id="m1", harness="claude"))
    metas = sorted((spool / "entries").glob("*.meta.json"))
    meta = json.loads(metas[0].read_text())
    meta["created_at"] = 1735689600  # a corrupt TYPE, not merely a corrupt value
    metas[0].write_text(json.dumps(meta))

    drain = _drain_tool()
    corpus, skipped = drain._spooled_corpus(spool, 5)
    assert len(corpus) == 2, corpus
    assert skipped == 0, skipped


def test_a_KeyboardInterrupt_during_authenticate_is_NOT_swallowed(
        monkeypatch, tmp_path):
    """The guard is `(Exception, SystemExit)` — never `BaseException`.

    Review cycle 12 (P3): widening the clause to `except BaseException` left the
    whole module green (69 passed), so the comment's "a `KeyboardInterrupt` still
    interrupts" had no witness and a future edit could swallow Ctrl-C into a clean
    exit 2.

    Mutant: `except BaseException` → the interrupt never reaches the caller and this
    row reds.
    """
    drain = _drain_tool()

    class _Stub:
        label = "stub"

        def __init__(self, *_a, **_kw):
            pass

        def authenticate(self, _org):
            raise KeyboardInterrupt

        def close(self):
            pass

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drain, "_InProcessTransport", _Stub)

    with pytest.raises(KeyboardInterrupt):
        drain.main(["--n", "1"])


def test_the_JSON_report_carries_the_SPOOL_SKIP_count(monkeypatch, capsys,
                                                      tmp_path):
    """The skip count must travel in the REPORT, not only on stderr.

    Review round R (P3): `--json` carried no key for the entries the corpus reader
    could not read, so the one path that reads a real spool reported its
    unreadable-entry count on stderr alone — a denominator that can shrink with
    nothing in the machine report saying it did.

    Mutant: drop the `corpus_skipped` key → this row reds (KeyError).
    """
    from tortoise.capture_spool import Snapshot, write_spool_entry

    spool = tmp_path / "spool"
    write_spool_entry(spool, Snapshot(
        session_id="acc-ok", turns=[{"role": "user", "content": "hi"}],
        source="acceptance-14232", machine_id="m1", harness="claude"))
    (spool / "entries" / "deadbeef.meta.json").write_text("{not json")

    drain = _criterion_drain(monkeypatch, tmp_path)
    rc = drain.main(["--sessions-dir", str(spool), "--n", "5",
                     "--max-ack-p95", "3600", "--json"])
    captured = capsys.readouterr()
    assert rc == 0, (rc, captured.err)
    report = json.loads(captured.out)
    assert report["n"] == 1, report["n"]
    assert report["corpus_skipped"] == 1, report["corpus_skipped"]
    assert "skipped 1 unreadable/empty spool entries" in captured.err, captured.err


def test_ENTITIES_REQUIRED_and_absent_fails_the_run(monkeypatch, capsys, tmp_path):
    """`ok_extract`'s `ok_entities` arm is a graded input, and it had no witness.

    Round Q (P2): `--require-entities` + `{points: 1, entities: 0, edges: 0}` is
    the defect criterion 4 exists to catch ("points exist but nothing was WIRED to
    them"), and no row in the file ever passed the flag — enabling
    `ok_entities = True` left 52/52 green. Mutant: `ok_entities = True` → this row
    reds with `VERDICT: PASS` at rc 0.
    """
    drain = _criterion_drain(
        monkeypatch, tmp_path,
        evidence=lambda sid: {"measured": True, "points": 1, "entities": 0,
                              "edges": 0, "session_id": sid})
    rc = drain.main(["--n", "1", "--max-ack-p95", "3600", "--require-entities"])
    out = capsys.readouterr().out
    assert rc == 1, f"a required-but-absent entity is a FAIL (exit 1): rc={rc}\n{out}"
    assert "[FAIL] extraction demonstrably ran" in out, out
    assert "VERDICT: FAIL" in out, out


def test_UNMEASURED_extraction_evidence_fails_the_run(monkeypatch, capsys,
                                                      tmp_path):
    """`evidence["measured"]` is the other half of `ok_extract`, also unwitnessed.

    Round Q (P2): no row passed `measured: False`, so an unmeasured claim counted as
    extraction with the file green. ⛔ The guard exists in TWO redundant places (the
    `and` in the expression and the `if not measured` arm below it), so reverting
    either ALONE is a no-op — measured. The mutant this row pins is the behaviour:
    drop `measured` from the expression AND delete the arm → PASS at rc 0, this row
    reds. A row can only pin a behaviour, not a redundancy.
    """
    drain = _criterion_drain(
        monkeypatch, tmp_path,
        evidence=lambda sid: {"measured": False, "points": 1, "session_id": sid})
    rc = drain.main(["--n", "1", "--max-ack-p95", "3600"])
    out = capsys.readouterr().out
    assert rc == 1, f"unmeasured evidence is a FAIL (exit 1): rc={rc}\n{out}"
    assert "[FAIL] extraction demonstrably ran" in out, out


def test_the_DEFERRAL_share_does_not_count_refused_acks(monkeypatch, capsys,
                                                        tmp_path):
    """The deferral numerator filters on `status_code == 200`; nothing pinned it.

    Round Q (P3): dropping that filter left 52/52 green. A refused ack is not an
    ack that DEFERRED, and without the filter a 504 body carrying
    `status: accepted` would inflate `n` past `of` — the share would report work
    the server never accepted. Mutant: drop `o["status_code"] == 200` from the
    `deferred` sum → this row reds (`n == 1` over `of == 0`).
    """
    class _RefusedButChatty:
        status_code = 504
        text = '{"status": "accepted", "extraction": {"phase": "extracting"}}'

        def json(self):
            # A 504 whose body still claims the ack was accepted — the shape the
            # numerator must NOT count.
            return {"status": "accepted", "extraction": {"phase": "extracting"},
                    "stage": "capture_gates"}

    drain = _criterion_drain(monkeypatch, tmp_path,
                             ack=lambda _cap: _RefusedButChatty())
    rc = drain.main(["--n", "1", "--max-ack-p95", "3600", "--json"])
    captured = capsys.readouterr()
    assert rc == 1, rc
    report = json.loads(captured.out)
    assert report["deferred_at_ack"]["n"] == 0, report["deferred_at_ack"]
    assert report["deferred_at_ack"]["of"] == 0, report["deferred_at_ack"]
    assert report["deferred_at_ack"]["measurable"] is False


def test_ENTITIES_present_but_EDGES_absent_fails_the_run(monkeypatch, capsys,
                                                         tmp_path):
    """`ok_entities` is a conjunction (`entities >= 1` AND `edges >= 1`), pinned.

    Round Q (P2) territory: with the entities arm unwitnessed, the *edges* conjunct
    had no row either — a point extracted with nothing WIRED to it is the defect
    criterion 4 names, and `edges: 0` alone must FAIL under `--require-entities`.
    Mutant: drop the `edges` conjunct from `ok_entities` → this row reds.
    """
    drain = _criterion_drain(
        monkeypatch, tmp_path,
        evidence=lambda sid: {"measured": True, "points": 1, "entities": 3,
                              "edges": 0, "session_id": sid})
    rc = drain.main(["--n", "1", "--max-ack-p95", "3600", "--require-entities"])
    out = capsys.readouterr().out
    assert rc == 1, f"entities without edges is a FAIL (exit 1): rc={rc}\n{out}"
    assert "[FAIL] extraction demonstrably ran" in out, out


def test_ENTITIES_absent_but_edges_present_fails_the_run(monkeypatch, capsys,
                                                         tmp_path):
    """`ok_entities`' `entities >= 1` conjunct, in the direction nobody pinned.

    Review round R (P3): `entities: 0, edges: 3` left every row green when the
    `entities >= 1` conjunct was deleted — the two existing rows use `0/0` and
    `3/0`, so neither isolates it. In process these counts come from two columns
    of ONE query, and a `:Entity` count that disagrees with its edge count is
    exactly the drift a conjunction exists to catch.

    Mutant: drop `(evidence.get("entities") or 0) >= 1` → this row reds (PASS).
    """
    drain = _criterion_drain(
        monkeypatch, tmp_path,
        evidence=lambda sid: {"measured": True, "points": 1, "entities": 0,
                              "edges": 3, "session_id": sid})
    rc = drain.main(["--n", "1", "--max-ack-p95", "3600", "--require-entities"])
    out = capsys.readouterr().out
    assert rc == 1, f"edges with no entity is a FAIL (exit 1): rc={rc}\n{out}"
    assert "[FAIL] extraction demonstrably ran" in out, out
    assert "VERDICT: FAIL" in out, out
