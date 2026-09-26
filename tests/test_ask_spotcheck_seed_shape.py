"""#3910 — ``ask_spotcheck._seed_memory`` must seed the CAPTURE shape.

Measured defect (pre-fix, verified by execution): the spot-check fixture
seeded a graph the real capture path cannot produce — plain ``statement``
Points carrying ``p.sessionId`` / ``p.eventId`` PROPS and **no edge at all**.
Capture instead writes deterministic ``f"{sid}_t{i}"`` episodic turn Points
with NO ``sessionId``/``eventId`` prop, and wires provenance with
``MERGE (s)-[:CONTAINS]->(t)`` — which is what the shipping read resolves
identity from (``OPTIONAL MATCH (sess:Session)-[:CONTAINS]->(n)``). The
shipping fetch PREFERS a renderable ``p.sessionId`` prop, so a consumer that
read that prop reported GREEN on a graph where the CONTAINS-edge path was
broken: the fixture taught the wrong shape.

These tests assert on what the fixture WRITES (graph shape) and on the VALUE
the shipping read returns for it — never a grep of source text. One test is a
MUTATION PROOF: it deletes the ``CONTAINS`` edge and pins that the identity
is then GONE, so the positive assertions cannot pass vacuously.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ask_spotcheck import _seed_memory  # noqa: E402, RUF100
from tortoise.ask_lane import (  # noqa: E402, RUF100
    _reset_ask_reader_cache_for_tests,
    run_ask_lane,
)
from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100

#: A question in the committed composition's schema (the keys
#: ``_seed_memory`` consumes), small enough to seed per test.
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

#: The fixture's OWN session ids — the identity the seeded turns must carry
#: (capture keys a session by a client-supplied id or its own server-minted
#: ``session_<hex12>`` id, never a synthetic ``sess-N``).
SID_0, SID_1 = QUESTION["haystack_session_ids"]
TURNS_0 = [f"{SID_0}_t0", f"{SID_0}_t1"]
ALL_TURNS = [*TURNS_0, f"{SID_1}_t0"]


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


@pytest.fixture
def seeded(tmp_path):
    """A seeded temp DB, closed and removed by pytest's tmp_path teardown."""
    sdk = TortoiseSDK(str(tmp_path / "t.db"))
    try:
        _seed_memory(sdk, QUESTION)
        yield sdk
    finally:
        sdk.close()


def _install_fake_reader(monkeypatch) -> _FakeReader:
    import tortoise.ask_lane as sdk_mod

    fake = _FakeReader()
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
    return fake


def _wire(sdk: TortoiseSDK, query: str, *, want: set[str],
          attempts: int = 3) -> list[dict]:
    """The shared point fetch behind ``tortoise_fts_query`` / ``/v1/search``.

    Retried until the REQUIRED ids are present — not merely until the pool is
    non-empty: the embedded engine degrades PER STRATEGY ("one strategy down,
    others continue"), so a partial pool is the same flake class as the empty
    one, and PR #3888 recorded the load event as
    `Strategies timed out (500ms) — collected 0/3`.
    """
    hits: list[dict] = []
    for _ in range(attempts):
        hits = sdk.tortoise_fts_query(query, limit=40,
                                      include_terminal=True)
        if want <= {str(h.get("id")) for h in hits}:
            return hits
    return hits


def _ask(sdk: TortoiseSDK, query: str, *, want_evidence: str,
         want_ids: set[str] | None = None, attempts: int = 3) -> dict:
    """``run_ask_lane`` on the eval-only local lane, retried (same flake
    class as ``_wire``) until the reader's context carries the seeded text
    AND — when ``want_ids`` is given — the identity set under test.

    Retrying on the ASSERTED value matters in both directions: a PARTIAL
    pool (one strategy down, others continue) would otherwise read as a
    false RED on the green leg, and an EMPTY pool would read as "the
    identity is genuinely gone" on the mutated leg — a vacuous pass.
    """
    result: dict = {"evidence": "", "retrieved_session_ids": []}
    for _ in range(attempts):
        result = run_ask_lane(sdk, query, question_date="2023-05-22")
        ids = set(result.get("retrieved_session_ids") or [])
        if want_evidence in result.get("evidence", "") and (
                want_ids is None or ids == want_ids):
            return result
    return result


# ── 1. The fixture WRITES the capture shape ───────────────────────────────

def test_seeded_turn_points_are_capture_shaped(seeded):
    """Every haystack turn lands as capture writes it: a ``:Session``, an
    episodic ``pointKind='event'`` turn Point with the deterministic
    ``f"{sid}_t{i}"`` id and a ``speaker``, and a real
    ``(:Session)-[:CONTAINS]->(:Point)`` edge — and NO ``sessionId`` /
    ``eventId`` prop (the seeder's old, unforgeable-elsewhere provenance)."""
    proj = seeded._get_proj()

    points = proj.g.query(
        "MATCH (p:Point) RETURN p.id, p.pointKind, p.is_episodic, p.speaker, "
        "       p.sessionId, p.eventId ORDER BY p.id",
    ).result_set
    assert [r[0] for r in points] == sorted(ALL_TURNS), points
    for pid, kind, episodic, speaker, sess_prop, ev_prop in points:
        assert kind == "event", (pid, kind)
        assert episodic is True, (pid, episodic)
        assert speaker in ("user", "assistant"), (pid, speaker)
        # THE DEFECT: provenance must NOT ride a prop the capture path never
        # writes — a consumer reading p.sessionId must find nothing.
        assert sess_prop is None, (pid, sess_prop)
        assert ev_prop is None, (pid, ev_prop)

    # The :Session nodes carry the FIXTURE's own ids (not a synthetic
    # sess-N) — the identity the shipping read now reports is the one the
    # question's gold sessions are keyed by.
    sessions = proj.g.query(
        "MATCH (s:Session) RETURN s.id ORDER BY s.id").result_set
    assert [r[0] for r in sessions] == sorted([SID_0, SID_1]), sessions

    # ... and the provenance edge IS there, one per turn.
    edges = proj.g.query(
        "MATCH (s:Session)-[:CONTAINS]->(p:Point) "
        "RETURN s.id, p.id ORDER BY s.id, p.id",
    ).result_set
    assert [list(r) for r in edges] == sorted(
        [[SID_0, TURNS_0[0]], [SID_0, TURNS_0[1]], [SID_1, f"{SID_1}_t0"]]), \
        edges


# ── 2. MUTATION PROOF: the CONTAINS edge IS the identity ──────────────────

def test_identity_resolves_from_the_edge_and_vanishes_without_it(seeded,
                                                                 monkeypatch):
    """The fixture is faithful when the SHIPPING read resolves the seeded
    session from the ``CONTAINS`` edge — and RED when that edge is deleted.

    Both readers are the existing internal ones: ``run_ask_lane`` (the eval
    lane's structured ``retrieved_session_ids`` + rendered evidence tags) and
    the shared point fetch behind ``tortoise_fts_query`` (the ``/v1/search``
    payload). No new surface is exercised.
    """
    _install_fake_reader(monkeypatch)
    question = "what is the gym schedule?"
    seeded_text = "the gym schedule is Monday and Wednesday"

    # GREEN — the seeded shape resolves the identity on both read paths.
    result = _ask(seeded, question, want_evidence=seeded_text,
                  want_ids={SID_0, SID_1})
    assert seeded_text in result["evidence"], (
        "precondition: the reader's context must carry the seeded turn — "
        f"got {result['evidence']!r}")
    assert set(result["retrieved_session_ids"]) == {SID_0, SID_1}, result.get(
        "retrieved_session_ids")
    assert f"[session {SID_0}]" in result["evidence"]
    assert f"[session {SID_1}]" in result["evidence"]
    assert "[session ?]" not in result["evidence"]

    wire = _wire(seeded, question, want=set(TURNS_0))
    ours = [h for h in wire if h["id"] in TURNS_0]
    assert sorted(h["id"] for h in ours) == sorted(TURNS_0), (
        "precondition: both seeded turns must be retrievable — got "
        f"{[h.get('id') for h in wire]!r}")
    assert [h["sessionId"] for h in ours] == [SID_0, SID_0], ours

    # MUTATION — delete the ONLY provenance mechanism (capture writes no
    # sessionId/eventId prop, so nothing else can carry the identity).
    seeded._get_proj().g.query(
        "MATCH (:Session)-[r:CONTAINS]->(:Point) DELETE r",
    )

    # The turns must SURVIVE the edge delete before the identity assertion
    # means anything (checked first, with the retry, so an empty/partial pool
    # cannot masquerade as "the identity is gone").
    wire_mut = _wire(seeded, question, want=set(TURNS_0))
    still_there = [h for h in wire_mut if h["id"] in TURNS_0]
    assert sorted(h["id"] for h in still_there) == sorted(TURNS_0), (
        "precondition: the turns must survive the edge delete — got "
        f"{[h.get('id') for h in wire_mut]!r}")
    assert all(h["sessionId"] == "" for h in still_there), still_there

    mutated = _ask(seeded, question, want_evidence=seeded_text)
    assert seeded_text in mutated["evidence"], (
        "precondition: the reader's context must still carry the seeded turn "
        f"after the edge delete — got {mutated['evidence']!r}")
    assert mutated["retrieved_session_ids"] == [], (
        "the CONTAINS edge is gone but the ask lane still names a session — "
        f"the identity is not derived from that edge: "
        f"{mutated.get('retrieved_session_ids')!r}")
    assert f"[session {SID_0}]" not in mutated["evidence"]
    assert "[session ?]" in mutated["evidence"]


# ── 3. #4106: an UNDATED fixture session records NO time ──────────────────

def test_dateless_fixture_session_records_no_recorded_time(tmp_path):
    """#4106: a session the fixture does NOT date must record NO session time.

    The fixture tells the shared capture seeder ``now=None``, which since
    #4156 means "record NO time" rather than "use the run clock". The
    ask-path date annotation reads ``:Session.created_at``, so a run clock
    there would render as the session's date — a fabricated fact in front of
    a temporal question. NO recorded time is written (and nothing has to be
    erased afterwards), so the reader's context carries NO date marker.
    """
    from tools.ask_spotcheck import _seed_memory

    question = dict(QUESTION)
    # one blank date, one unparseable — both mean "not recorded"
    question["haystack_dates"] = ["", "not-a-date"]
    sdk = TortoiseSDK(str(tmp_path / "undated.db"))
    try:
        _seed_memory(sdk, question)
        proj = sdk._get_proj()
        sessions = proj.g.query(
            "MATCH (s:Session) RETURN s.id, s.created_at ORDER BY s.id"
        ).result_set
        assert [r[0] for r in sessions] == sorted([SID_0, SID_1]), sessions
        assert all(r[1] is None for r in sessions), sessions
        turns = proj.g.query(
            "MATCH (t:Point) RETURN t.createdAt").result_set
        assert turns and all(r[0] is None for r in turns), turns

        hits = sdk.tortoise_fts_query("gym schedule", limit=40,
                                      include_terminal=True)
        ann = sdk.annotate_ask_hits(hits)
        assert ann, "fixture must retrieve"
        assert all(not h.get("session_date") for h in ann), ann
        from tortoise.retrieval import render_context
        evidence = render_context(ann)
        assert "(session date" not in evidence, evidence
    finally:
        sdk.close()
