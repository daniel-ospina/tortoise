"""#3914 — every ask-lane fixture/seeder must seed the CAPTURE shape.

The defect class (#3910 fixed the first instance; #3914 owns the class): a
seeder MANUFACTURES the graph other code then reads, so a wrong shape does
not produce one bad point — it teaches every downstream consumer a shape the
real system does not use, and it can make a real defect invisible because the
consumer is tested against the forgery.

Concretely, the shipping point fetch resolves a hit's session identity as
``coalesce(p.sessionId prop, (:Session)-[:CONTAINS]->(p) id)`` — the PROP
FIRST. A fixture that wrote ``p.sessionId`` and no edge therefore stayed
byte-green even with the CONTAINS read removed entirely (the mutation #3888
recorded: ``retrieved_session_ids == []``).

These tests assert on the RESOLVED graph — labels, properties, and the exact
``(:Session)-[:CONTAINS]->(:Point)`` edge set read back out of the store —
never on seeder source text, and never on a consumer that a prop can satisfy.
The last test is a MUTATION PROOF on the committed transcripts: delete the
edge and the rendered evidence and the committed golden both change, so the
goldens are bound to the edge path rather than to a forgeable prop.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ask_spotcheck import seed_capture_turn_store
from tools.gen_ask_transcripts import (
    _render_user_message,
    _seed,
)
from tortoise.sdk import TortoiseSDK

TRANSCRIPTS_DIR = Path(__file__).parent / "fixtures" / "ask_llm_transcripts"
#: The transcript whose golden quotes the seeded session id directly
#: (``[session sess-1]``) — the live false-GREEN #3914 was filed on.
FIXTURE = "gold-verbatim-commit"


def _transcript() -> dict:
    with open(TRANSCRIPTS_DIR / f"{FIXTURE}.json") as f:
        return json.load(f)


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(str(tmp_path / "t.db"))
    try:
        yield s
    finally:
        s.close()


def _read_shape(sdk: TortoiseSDK) -> tuple[list, list, list]:
    """The RESOLVED shape read back from the store: Point rows (id, content,
    pointKind, is_episodic, is_operator, speaker, sessionId prop, eventId
    prop, content_hash), Session rows (id, created_at, turn_count,
    is_episodic — capture writes all three, so a bare-id Session is a node
    shape no product writer produces), and the exact CONTAINS edge set."""
    proj = sdk._get_proj()
    points = proj.g.query(
        "MATCH (p:Point) RETURN p.id, p.content, p.pointKind, p.is_episodic, "
        "       p.is_operator, p.speaker, p.sessionId, p.eventId, "
        "       p.content_hash ORDER BY p.id",
    ).result_set
    sessions = proj.g.query(
        "MATCH (s:Session) RETURN s.id, s.created_at, s.turn_count, "
        "       s.is_episodic ORDER BY s.id").result_set
    edges = proj.g.query(
        "MATCH (s:Session)-[:CONTAINS]->(p:Point) RETURN s.id, p.id "
        "ORDER BY s.id, p.id").result_set
    return points, sessions, edges


def _render(sdk: TortoiseSDK, tx: dict) -> tuple[str, str]:
    """The generator's own render step, retried (the embedded engine degrades
    PER STRATEGY — "one strategy down, others continue"; #3888 recorded
    `Strategies timed out (500ms) — collected 0/3`). The retry is on the
    PROPERTY under test (the seeded text is present), never on mere
    non-emptiness."""
    msg, evidence = "", ""
    for _ in range(3):
        msg, evidence, _qtype = _render_user_message(
            sdk, tx["question"], tx["question_date"], tx["seeds"])
        if "office hours policy is 9am" in evidence:
            return msg, evidence
    return msg, evidence


# ── 1. The shared write path is capture-exact ─────────────────────────────

def test_seed_capture_turn_store_is_capture_exact(sdk):
    """``seed_capture_turn_store`` — the ONE seeder every ask fixture now
    writes through — emits exactly what ``TortoiseSDK.capture_session``'s turn
    loop emits: the ``{sid}_t{i}`` episodic Point per WINDOWED turn, the
    ``[role] <content>`` framing, the role normalization, and the CONTAINS
    edge. A blank turn IS stored (capture writes ``"[user] "``); a wholly
    blank session writes NOTHING (capture's gate is pre-mutation)."""
    ids = seed_capture_turn_store(sdk, "sess-x", [
        {"role": "user", "content": "hello"},
        {"role": None, "content": "no role"},
        {"role": "assistant", "content": 123},   # truthy non-string -> "123"
        {"role": "user", "content": ""},         # blank turn IS stored
        {"role": "user", "content": "z" * 6000},  # windowed at 5000
    ])
    assert ids == [f"sess-x_t{i}" for i in range(5)]

    points, sessions, edges = _read_shape(sdk)
    assert [r[0] for r in points] == ids
    assert [r[1] for r in points] == [
        "[user] hello",
        "[unknown] no role",
        "[assistant] 123",
        "[user] ",
        "[user] " + "z" * 5000,
    ]
    for pid, _c, kind, episodic, is_op, speaker, sess_prop, ev_prop, chash \
            in points:
        assert kind == "event", (pid, kind)
        assert episodic is True, (pid, episodic)
        assert is_op is False, (pid, is_op)
        assert speaker in ("user", "unknown", "assistant"), (pid, speaker)
        # THE DEFECT: no provenance may ride a prop capture never writes.
        assert sess_prop is None, (pid, sess_prop)
        assert ev_prop is None, (pid, ev_prop)
        assert chash, (pid, chash)

    assert [r[0] for r in sessions] == ["sess-x"]
    # The SESSION side is part of capture's shape too: all three props, with
    # turn_count over the window actually stored. A bare-id Session is a node
    # capture never writes, and consumers that read these props (the hosted
    # session listing reads s.turn_count; commit reads s.is_episodic) would
    # see None on a graph seeded without them.
    sid, created_at, turn_count, is_episodic = sessions[0]
    assert sid == "sess-x"
    assert created_at, sessions[0]
    assert turn_count == len(ids), sessions[0]
    assert is_episodic is True, sessions[0]
    assert [list(r) for r in edges] == [["sess-x", i] for i in ids]

    # Pre-mutation blank gate: nothing at all is written for a blank session.
    assert seed_capture_turn_store(
        sdk, "sess-blank", [{"role": "user", "content": ""}]) == []
    after = sdk._get_proj().g.query(
        "MATCH (s:Session {id:'sess-blank'}) RETURN count(s)").result_set
    assert after[0][0] == 0


# ── 2. The transcript seeder writes the capture shape ─────────────────────

def test_transcript_seeder_writes_the_capture_shape(sdk):
    """``tools/gen_ask_transcripts._seed`` (and its former mirror in
    ``tests/test_ask_regression_llm.py``, now an import) wrote plain
    ``statement`` Points carrying ``p.sessionId`` / ``p.eventId`` PROPS and NO
    edge. Read back what it writes now."""
    tx = _transcript()
    _seed(sdk, tx["seeds"])

    points, sessions, edges = _read_shape(sdk)
    n = len(tx["seeds"])
    expected_sessions = [f"sess-{i}" for i in range(n)]
    expected_turns = [f"sess-{i}_t0" for i in range(n)]

    assert [r[0] for r in points] == expected_turns
    for row, seed in zip(points, tx["seeds"], strict=True):
        (pid, content, kind, episodic, is_op, speaker,
         sess_prop, ev_prop, chash) = row
        assert kind == "event", (pid, kind)
        assert episodic is True, (pid, episodic)
        assert is_op is False, (pid, is_op)
        assert speaker == "user", (pid, speaker)
        assert content == f"[user] {seed['content']}", (pid, content)
        # THE DEFECT, on the lane whose goldens quote the identity.
        assert sess_prop is None, (pid, sess_prop)
        assert ev_prop is None, (pid, ev_prop)
        assert chash, (pid, chash)

    assert [r[0] for r in sessions] == expected_sessions
    for sid, created_at, turn_count, is_episodic in sessions:
        assert created_at, (sid, created_at)
        assert turn_count == 1, (sid, turn_count)
        assert is_episodic is True, (sid, is_episodic)
    assert [list(r) for r in edges] == [
        [s, t] for s, t in zip(expected_sessions, expected_turns, strict=True)]


# ── 3. MUTATION PROOF: the committed golden is bound to the CONTAINS edge ─

def test_transcript_golden_pins_identity_from_the_contains_edge(sdk):
    """GREEN: the committed transcript IS what this capture-shaped graph
    renders, identity included. MUTATED: delete the CONTAINS edge — the only
    provenance the capture path writes — and both the rendered tag and the
    committed golden change. So a reverted seeder (or a broken CONTAINS read)
    cannot leave the transcript suite byte-green, which is exactly what the
    prop-forged fixture did (#3888's killed mutation)."""
    tx = _transcript()
    _seed(sdk, tx["seeds"])

    msg, evidence = _render(sdk, tx)
    assert "office hours policy is 9am" in evidence, evidence
    assert "[session sess-1]" in evidence
    assert "[session ?]" not in evidence
    # The golden is generated-and-compared on THIS shape, byte for byte.
    assert msg == tx["user_message"]

    sdk._get_proj().g.query(
        "MATCH (:Session)-[r:CONTAINS]->(:Point) DELETE r")

    mutated_msg, mutated_ev = _render(sdk, tx)
    assert "office hours policy is 9am" in mutated_ev, (
        "precondition: the turn must SURVIVE the edge delete — an empty pool "
        f"would make the identity assertion vacuous. got {mutated_ev!r}")
    assert "[session ?]" in mutated_ev, mutated_ev
    assert "[session sess-1]" not in mutated_ev
    assert mutated_msg != tx["user_message"], (
        "the committed golden renders identically with the CONTAINS edge gone "
        "— it is NOT bound to the identity read")


# ── 4. #4106: a seed with NO session_date records NO time ────────────────

def test_transcript_seed_without_a_date_records_no_time(sdk):
    """#4106: ``_seed``'s date default must not become a session's recorded
    time. ``seed_capture_turn_store``'s ``now=None`` default is the RUN clock,
    which the ask-path date annotation would render as the session's date; a
    seed with no ``session_date`` therefore erases it (session AND turns) and
    the reader's context carries no date marker."""
    from tools.gen_ask_transcripts import _seed
    from tortoise.retrieval import render_context

    _seed(sdk, [{"content": "I bought a smoker today"}])  # no session_date
    proj = sdk._get_proj()
    sessions = proj.g.query(
        "MATCH (s:Session) RETURN s.id, s.created_at").result_set
    assert sessions and all(r[1] is None for r in sessions), sessions
    turns = proj.g.query("MATCH (t:Point) RETURN t.createdAt").result_set
    assert turns and all(r[0] is None for r in turns), turns
    hits = sdk.tortoise_fts_query("smoker", limit=40, include_terminal=True)
    ann = sdk.annotate_ask_hits(hits)
    assert ann and all(not h.get("session_date") for h in ann), ann
    assert "(session date" not in render_context(ann)
