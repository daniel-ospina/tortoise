"""#3910 — ``ask_spotcheck._seed_memory`` must seed the CAPTURE shape.

Measured defect (pre-fix, verified by execution): the spot-check fixture
seeded a graph the real capture path cannot produce — plain ``statement``
Points carrying ``p.sessionId`` / ``p.eventId`` PROPS and **no edge at all**.
Capture instead writes deterministic ``f"{sid}_t{i}"`` episodic turn Points
with NO ``sessionId``/``eventId`` prop, and wires provenance with
``MERGE (s)-[:CONTAINS]->(t)`` — which is what the shipping read resolves
identity from (``OPTIONAL MATCH (sess:Session)-[:CONTAINS]->(n)``). A
consumer that read ``p.sessionId`` therefore reported GREEN on a graph where
the CONTAINS-edge path was broken: the fixture taught the wrong shape.

These tests assert on what the fixture WRITES (graph shape) and on the VALUE
the shipping read returns for it — never a grep of source text. One test is a
MUTATION PROOF: it deletes the ``CONTAINS`` edge and pins that the identity
is then GONE, so the positive assertions cannot pass vacuously.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ask_spotcheck import _seed_memory  # noqa: E402, RUF100
from tortoise.sdk import (  # noqa: E402, RUF100
    TortoiseSDK,
    _reset_ask_reader_cache_for_tests,
)

#: A question in the committed composition's schema (the keys
#: ``_seed_memory`` consumes), small enough to seed twice per test.
QUESTION = {
    "question_id": "seed-shape-1",
    "question": "what is the gym schedule?",
    "question_type": "single-session-user",
    "question_date": "2023/05/22 (Mon) 09:00",
    "answer": "Monday and Wednesday",
    "haystack_session_ids": ["c694fe7b_1", "a8e8717b"],
    "answer_session_ids": ["c694fe7b_1"],
    "haystack_dates": ["2023/05/20 (Sat) 15:08", "2023/05/21 (Sun) 00:51"],
    "haystack_sessions": [
        [
            {"role": "user",
             "content": "the gym schedule is Monday and Wednesday"},
            {"role": "assistant",
             "content": "noted, the gym schedule is Monday and Wednesday"},
        ],
        [
            {"role": "user", "content": "the gym schedule moved to Thursday"},
        ],
    ],
}

#: The capture loop's id prefix for the seeded sessions (``sess-{i}``).
SID_0, SID_1 = "sess-0", "sess-1"
TURNS_0 = [f"{SID_0}_t0", f"{SID_0}_t1"]


class _FakeReader:
    """The ask lane's ONE reader call, stubbed (local lane, no provider)."""

    last_completion_tokens = 12

    def complete(self, *, system: str, user: str) -> str:
        return "Monday and Wednesday."

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clean_ask_state(monkeypatch):
    # The ambient shell may carry TORTOISE_API_URL (fleet env) — the local
    # lane is the surface under test here.
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    _reset_ask_reader_cache_for_tests()
    yield
    _reset_ask_reader_cache_for_tests()


def _new_sdk() -> TortoiseSDK:
    db = os.path.join(tempfile.mkdtemp(prefix="seed_shape_"), "t.db")
    return TortoiseSDK(db)


def _install_fake_reader(monkeypatch) -> _FakeReader:
    import tortoise.sdk as sdk_mod

    fake = _FakeReader()
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
    return fake


def _seeded() -> TortoiseSDK:
    sdk = _new_sdk()
    _seed_memory(sdk, QUESTION)
    return sdk


# ── 1. The fixture WRITES the capture shape ───────────────────────────────

def test_seeded_turn_points_are_capture_shaped():
    """Every haystack turn lands as capture writes it: a ``:Session``, an
    episodic ``pointKind='event'`` turn Point with the deterministic
    ``f"{sid}_t{i}"`` id and a ``speaker``, and a real
    ``(:Session)-[:CONTAINS]->(:Point)`` edge — and NO ``sessionId`` /
    ``eventId`` prop (the seeder's old, unforgeable-elsewhere provenance)."""
    sdk = _seeded()
    proj = sdk._get_proj()

    points = proj.g.query(
        "MATCH (p:Point) RETURN p.id, p.pointKind, p.is_episodic, p.speaker, "
        "       p.sessionId, p.eventId ORDER BY p.id",
    ).result_set
    all_turns = [*TURNS_0, f"{SID_1}_t0"]
    assert [r[0] for r in points] == all_turns, points
    for pid, kind, episodic, speaker, sess_prop, ev_prop in points:
        assert kind == "event", (pid, kind)
        assert episodic is True, (pid, episodic)
        assert speaker in ("user", "assistant"), (pid, speaker)
        # THE DEFECT: provenance must NOT ride a prop the capture path never
        # writes — a consumer reading p.sessionId must find nothing.
        assert sess_prop is None, (pid, sess_prop)
        assert ev_prop is None, (pid, ev_prop)

    # ... and the provenance edge IS there, one per turn.
    sessions = proj.g.query("MATCH (s:Session) RETURN s.id ORDER BY s.id").result_set
    assert [r[0] for r in sessions] == [SID_0, SID_1], sessions
    edges = proj.g.query(
        "MATCH (s:Session)-[:CONTAINS]->(p:Point) "
        "RETURN s.id, p.id ORDER BY s.id, p.id",
    ).result_set
    assert [list(r) for r in edges] == [
        [SID_0, TURNS_0[0]], [SID_0, TURNS_0[1]],
        [SID_1, f"{SID_1}_t0"]], edges


# ── 2. MUTATION PROOF: the CONTAINS edge IS the identity ──────────────────

def test_identity_resolves_from_the_edge_and_vanishes_without_it(monkeypatch):
    """The fixture is faithful when the SHIPPING read resolves the seeded
    session from the ``CONTAINS`` edge — and RED when that edge is deleted.

    Both readers are the existing internal ones: ``sdk.ask`` (the product
    lane's structured ``retrieved_session_ids`` + rendered evidence tags) and
    the shared point fetch behind ``tortoise_fts_query`` (the ``/v1/search``
    payload). No new surface is exercised.
    """
    sdk = _seeded()
    _install_fake_reader(monkeypatch)
    question = "what is the gym schedule?"

    # GREEN — the seeded shape resolves the identity on both read paths.
    result = sdk.ask(question, question_date="2023-05-22")
    assert set(result["retrieved_session_ids"]) == {SID_0, SID_1}, result.get(
        "retrieved_session_ids")
    assert f"[session {SID_0}]" in result["evidence"]
    assert f"[session {SID_1}]" in result["evidence"]
    assert "[session ?]" not in result["evidence"]

    wire = sdk.tortoise_fts_query(question, limit=40, include_terminal=True)
    ours = [h for h in wire if h["id"] in TURNS_0]
    assert [h["id"] for h in ours] == TURNS_0, [h.get("id") for h in wire]
    assert [h["sessionId"] for h in ours] == [SID_0, SID_0], ours

    # MUTATION — delete the ONLY provenance mechanism (capture writes no
    # sessionId/eventId prop, so nothing else can carry the identity).
    sdk._get_proj().g.query(
        "MATCH (:Session)-[r:CONTAINS]->(:Point) DELETE r",
    )

    mutated = sdk.ask(question, question_date="2023-05-22")
    assert mutated["retrieved_session_ids"] == [], (
        "the CONTAINS edge is gone but the ask lane still names a session — "
        f"the identity is not derived from that edge: "
        f"{mutated.get('retrieved_session_ids')!r}")
    assert f"[session {SID_0}]" not in mutated["evidence"]
    assert "[session ?]" in mutated["evidence"]

    wire_mut = sdk.tortoise_fts_query(question, limit=40,
                                      include_terminal=True)
    still_there = [h for h in wire_mut if h["id"] in TURNS_0]
    assert [h["id"] for h in still_there] == TURNS_0, (
        "precondition: the turns must survive the edge delete — got "
        f"{[h.get('id') for h in wire_mut]!r}")
    assert [h["sessionId"] for h in still_there] == ["", ""], still_there
