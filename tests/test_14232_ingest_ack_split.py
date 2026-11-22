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
#: no pre-existing
#: assertion races a deadline), so by the time a test body runs the module attribute
#: is no longer the deployed value. Reading it at import — before any fixture runs —
#: is what makes the row's claim ("a value too tight would keep every suite green")
#: about the REAL constant: a literal here would keep passing while
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
    capture beats a 0.5 s
    deadline is a question about HOST LOAD and `#5049` rule 1 forbids a deadline
    expiry from producing a verdict) and the offline mock extractor, completion
    lands inside it and the caller gets the SAME body shape as before this
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

    # ⛔ THE GATES PHASE MUST BE CHEAP FOR THIS ROW TO BE ATTRIBUTABLE (#5049
    # rule 1, `tests/conftest.py`'s own fixture docstring). The bound below is
    # 0.35 s and the write is made to overrun it; the gates phase runs FIRST, and
    # on an un-stubbed tree it contains `count_org_usage(org, "points")` — a
    # FULL-GRAPH scan (`hosted_api.py:20796`, which this PR's plan calls out as
    # pre-existing and out of scope). When that scan alone exceeds 0.35 s the
    # refusal correctly names `capture_gates` and the row reds — for a reason
    # that is about the host, not about the `capture_write` mark it exists to
    # pin. Measured (review round 4, at the parent head): 4 failed / 1 passed
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
    # exception text and never generic prose. Mutants that red it: fold `detail`
    # from the raised exception, or swap the constant for new wording. The old
    # line (`"str(slow)" not in body.get("detail", "")`) could not vary under ANY
    # mutation and advertised a check that did not exist.
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
    `min(window, _TRANSPORT_WAIT_BOUND_S / 2)` = `min(30, 5)` = **5 s**, and a cold
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
