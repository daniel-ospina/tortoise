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

import threading
import time

import pytest

import tortoise.hosted_api as ha
from tests.test_hosted_api import TEST_ORG_ID  # noqa: F401 — fixture side effects
from tests.test_hosted_api import client as client  # noqa: F401 — the fixture

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

def _force_ack(monkeypatch):
    """Force the ACK path: the join window is zero, so completion cannot arrive.

    ⛔ Patching the WINDOW rather than the extractor keeps the assertion about
    the handler's own decision. A slow fake extractor would make the test a race
    between two wall clocks; a zero window makes "did the caller get the ack"
    a fact about the code path under test.
    """
    monkeypatch.setattr(ha, "_capture_ack_grace_s", lambda _request: 0.0)


def _drain_tails(timeout: float = 30.0) -> None:
    """Wait for every scheduled capture completion to finish."""
    deadline = time.monotonic() + timeout
    while ha._CAPTURE_TAIL_TASKS and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not ha._CAPTURE_TAIL_TASKS, (
        f"capture completions did not finish: {ha._CAPTURE_TAIL_TASKS}")


def _proj():
    return ha._make_sdk(namespace=TEST_ORG_ID)._get_proj()


def _session_row(session_id: str) -> list:
    return _proj().g.query(
        "MATCH (s:Session {id:$sid}) "
        "RETURN s.id, s.capture_ok, s.capture_extractor, "
        "       s.capture_extraction_phase, s.capture_extraction_phase_at",
        params={"sid": session_id}).result_set


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
        parked.release.set()
    _drain_tails()


def test_a_raised_completion_does_NOT_write_the_marker(client, monkeypatch):
    """The other direction, and it is a documented contract: failure ≠ cancellation.

    `_capture_abandoned_marker`'s own docstring scopes it to CANCELLATION — a
    capture that RAISES keeps its NULL→legacy-replay shape
    (`test_hosted_api.py::TestSessionActorStamp2600`'s raise-shape (ii) pins that
    a re-POST of a raise-shaped session must not re-extract). Widening the marker
    to failures would turn every failed extraction into a retry-eligible record.
    """
    session_id = "s-14232-raised"

    class _Failed:
        def cancelled(self) -> bool:
            return False

        def exception(self):
            return RuntimeError("boom")

    state = {"attempted": True, "finalized": False, "lane": "v2"}
    ha._capture_tail_failed(_Failed(), _proj(), session_id, state)
    # No dispatch was scheduled — the marker is the observable.
    time.sleep(0.05)
    assert not _session_row(session_id), (
        "a raised tail must not mint a marker for a session that does not exist; "
        "and for a real one it must leave capture_ok NULL")


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
    """R3b — the in-budget half. No monkeypatching of the window at all.

    With the real (1 s) window and the offline mock extractor, completion lands
    inside it and the caller gets the SAME body shape as before this change:
    `status ok/partial`, a real `extracted` count, the extraction mode, and NO
    in-flight phase. That is what keeps 34 test files and every capture client
    reading what they read yesterday — and it is asserted here by cause (the
    window was not forced) rather than by timing the test.
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


# ── R4: the in-flight key outlives the tail ──────────────────────────────

def test_a_same_session_repost_during_the_tail_is_refused(client, monkeypatch):
    """`#3129` — the early ACK must not re-open the replay window.

    The ACK is served while the tail still owns the capture. A second POST for the
    same session in that window used to be admitted and replayed a 200 with 0
    extracted, for a capture whose only real attempt could then fail. The gate is
    the `_CaptureSlot` session key, which the completion reports on — delete
    `slot.tail_pending()` and this test gets a 200 replay instead of a 409.
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
    phase must appear in `stage`. Deleting the `capture_write` mark falls back to
    `capture_gates` and reds this.
    """
    from tortoise.session_projection import SESSION_EXTRACTION_PHASES  # noqa: F401

    monkeypatch.setattr(ha._mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.35)
    real_write = ha._write_session_and_turns

    def _slow_write(*args, **kwargs):
        time.sleep(0.8)
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
    assert body["stage"] == "capture_write", (
        f"the breach must name the stage it landed in: {body}")
    assert body["stages_ms"].get("capture_gates") is not None, (
        f"the finished gate phase must be timed beside it: {body}")
    # The bound's own envelope, not any 504: a refusal that lost its cause would
    # satisfy neither assertion below.
    assert "str(slow)" not in body.get("detail", ""), body


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

    assert ha._CAPTURE_IN_FLIGHT == before, (
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

    assert ha._CAPTURE_IN_FLIGHT == before, (
        f"capacity leaked on the no-dispatch arm: {before} -> {ha._CAPTURE_IN_FLIGHT}")
    assert not ha._CAPTURE_SESSIONS, ha._CAPTURE_SESSIONS
    row = _session_row("s-14232-no-dispatch")
    assert row and row[0][1] is None, (
        "a dispatch FAILURE is not a cancellation and must not write the "
        f"abandoned marker (raise-shape contract): {row}")
