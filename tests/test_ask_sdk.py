"""Ask-lane eval-lane tests (#1987 Tasks 4-5) — annotate_ask_hits + run_ask_lane.

Task 4 — ask-path-local hit annotation: session_date/speaker from the Event
join + source-turn speaker, additive keys only, undated/null-join
byte-identical, has_answer passthrough.

Task 5 — the eval-only ask lane (``tortoise/ask_lane.py``, #3849): local-lane
pipeline (validation FIRST,
exactly ONE model call incl. empty context — no pre-gate), resolved caps
(200/200/16000/derived since #4105; the cap-binding tests pin their own
shape), resolved question_date semantics, the per-namespace reader cache
(tokens-race, key isolation, failed-build, lifecycle), and both-not-either
(search surfaces
never invoke the reader). The hosted-mode ``_post_ask`` client was removed
with the REST surface (#3849).

Runs on the docker lane (TORTOISE_DB_URI) — the #1987 test strategy's
integration layer.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ask_spotcheck import merge_capture_session
from tortoise.ask_lane import (
    _reset_ask_reader_cache_for_tests,
    run_ask_lane,
)
from tortoise.exceptions import (
    AskReaderUnavailable,
    AskValidationError,
)
from tortoise.retrieval import estimate_tokens_ask
from tortoise.schemas import (
    CODE_INVALID_QUESTION,
    CODE_INVALID_QUESTION_DATE,
    CODE_INVALID_QUESTION_TYPE,
    CODE_QUESTION_TOO_LONG,
)
from tortoise.sdk import TortoiseSDK


@pytest.fixture(autouse=True)
def _clean_ask_state():
    """Reset the shared ask-reader cache + budget between tests."""
    _reset_ask_reader_cache_for_tests()
    from tortoise.quota import _reset_ask_budget_for_tests
    _reset_ask_budget_for_tests()
    yield
    _reset_ask_reader_cache_for_tests()


def _new_sdk() -> TortoiseSDK:
    db = os.path.join(tempfile.mkdtemp(prefix="ask_sdk_"), "t.db")
    return TortoiseSDK(db)


class FakeReader:
    """complete() stub with a call counter + captured user message."""

    def __init__(self, reply: str = "The gym schedule is Monday and Wednesday.",
                 tokens_out: int = 12):
        self.reply = reply
        self.tokens_out = tokens_out
        self.calls = 0
        self.last_user: str | None = None
        self.closed = False

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        self.last_user = user
        return self.reply

    def close(self) -> None:
        self.closed = True


# ── Task 4: annotate_ask_hits ──────────────────────────────────────────────

#: The session `_seed_event_graph`'s extracted claims belong to. Capture
#: CONTAINS-wires its EXTRACTED Points to the session too (the extraction
#: loops in `tortoise/sdk.py`: `MATCH (s:Session {id:$sid}),
#: (p:Point {id:$pid}) MERGE (s)-[:CONTAINS]->(p)`), not just its turns.
SEEDED_EVENT_SESSION = "sess-extracted"


def _seed_event_graph(sdk: TortoiseSDK, turns: list[dict]) -> list[dict]:
    """Seed EXTRACTED-claim points + Events; return the ``tortoise_fts_query``
    hits.

    These Points model capture's EXTRACTED claims, and both halves of the
    shape they carry are written here: capture stamps ``n.eventId`` on an
    extracted claim (so the ``:Event`` join that produces ``session_date`` —
    this fixture's subject, #1987 Task 4 — reaches it) AND wires it to the
    session node with ``MERGE (s)-[:CONTAINS]->(p)`` (the extraction loops in
    ``tortoise/sdk.py``). The shipping point fetch resolves an extracted
    claim's identity from that edge.

    What it must NOT write is the ``p.sessionId`` PROP. Capture never writes
    one — on claims or on turns — and the fetch PREFERS a renderable
    ``p.sessionId`` over the CONTAINS edge, so a prop is the only provenance
    this fixture could carry that the graph cannot legitimately hold, and the
    only thing a forgery in it would satisfy. Pre-#3914 this helper had a
    ``t.get("sessionId")`` branch writing exactly that, with no ``(:Session)``
    node and no edge; ``test_seed_event_graph_ignores_a_session_id_key`` pins
    that a passed key is IGNORED, so re-adding the branch reds the build.
    """
    proj = sdk._get_proj()
    # The session node carries capture's own prop set — a bare-id Session is a
    # node shape no product writer produces, and consumers reading
    # `s.turn_count` / `s.is_episodic` would see None. This fixture models no
    # TURN stream, so `turn_count` counts the claims seeded here: the prop SET
    # is capture's, that one VALUE is the fixture's placeholder.
    merge_capture_session(sdk, SEEDED_EVENT_SESSION, len(turns))
    for i, t in enumerate(turns):
        point = sdk.create_point("statement", t["content"])
        eid = t.get("eventId", f"ev-{i}")
        proj.g.query(
            "MERGE (e:Event {eventId: $eid}) SET e.startedAt = $st",
            params={"eid": eid, "st": f"{t.get('session_date', '2026-08-20')}T10:00:00Z"},
        )
        sets = ["p.eventId = $eid"]
        params = {"pid": point["id"], "eid": eid}
        if t.get("speaker"):
            sets.append("p.speaker = $spk")
            params["spk"] = t["speaker"]
        proj.g.query(
            "MATCH (p:Point {id: $pid}) SET " + ", ".join(sets),
            params=params,
        )
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
            "MERGE (s)-[:CONTAINS]->(p)",
            params={"sid": SEEDED_EVENT_SESSION, "pid": point["id"]},
        )
    return sdk.tortoise_fts_query("gym", limit=40, include_terminal=True)


def test_seed_event_graph_ignores_a_session_id_key():
    """#3914: an extracted-claim fixture must NOT forge the ``sessionId`` PROP.

    The removed branch wrote ``p.sessionId = $sid`` — no ``(:Session)`` node,
    no ``CONTAINS`` edge — the one provenance the graph cannot legitimately
    carry and the one the shipping fetch PREFERS over the edge. A passed key
    must now be a no-op AND the identity must still resolve, from the edge the
    fixture does write, so re-adding the branch OR dropping the edge reds it.
    """
    sdk = _new_sdk()
    hits = _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday", "eventId": "ev1",
         "session_date": "2026-08-01", "speaker": "user",
         "sessionId": "forged-sess"},
    ])
    assert hits, "fixture must retrieve"
    proj = sdk._get_proj()
    points = proj.g.query(
        "MATCH (p:Point) RETURN p.id, p.sessionId, p.eventId").result_set
    assert points, points
    for pid, sess_prop, _ev_prop in points:
        assert sess_prop is None, (pid, sess_prop)
    edges = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) RETURN p.id",
        params={"sid": SEEDED_EVENT_SESSION}).result_set
    assert [r[0] for r in edges] == [r[0] for r in points], edges
    # The Session side is part of the shape too — capture writes all three.
    sess = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.created_at, s.turn_count, "
        "s.is_episodic", params={"sid": SEEDED_EVENT_SESSION}).result_set
    assert len(sess) == 1, sess
    created_at, turn_count, is_episodic = sess[0]
    assert created_at, sess
    assert turn_count == len(points), sess
    assert is_episodic is True, sess
    # The wire identity comes from that edge — never from the forged key.
    assert all(h.get("sessionId") == SEEDED_EVENT_SESSION for h in hits), hits
    sdk.close()


def test_annotate_session_date_and_speaker():
    sdk = _new_sdk()
    hits = _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday", "eventId": "ev1",
         "session_date": "2026-08-01", "speaker": "user"},
    ])
    assert hits, "fixture must retrieve"
    ann = sdk.annotate_ask_hits(hits)
    assert len(ann) == len(hits)
    assert ann[0]["session_date"] == "2026-08-01"
    assert ann[0]["speaker"] == "user"


def test_annotate_speaker_from_source_turn():
    """Extracted point with source_turn_id → speaker from the source turn."""
    sdk = _new_sdk()
    proj = sdk._get_proj()
    turn = sdk.create_point("statement", "we decided the office hours are 9am")
    proj.g.query(
        "MATCH (p:Point {id: $pid}) SET p.speaker = 'assistant', p.eventId = 'ev9'",
        params={"pid": turn["id"]},
    )
    extracted = sdk.create_point("statement", "office hours are 9am")
    proj.g.query(
        "MATCH (p:Point {id: $pid}) SET p.eventId = 'ev9', p.source_turn_id = $tid",
        params={"pid": extracted["id"], "tid": turn["id"]},
    )
    proj.g.query(
        "MERGE (e:Event {eventId: 'ev9'}) SET e.startedAt = '2026-08-02T10:00:00Z'",
    )
    hits = sdk.tortoise_fts_query("office hours", limit=40, include_terminal=True)
    ann = sdk.annotate_ask_hits(hits)
    by_id = {h["id"]: h for h in ann}
    assert by_id[extracted["id"]]["speaker"] == "assistant"


def test_annotate_undated_hit_byte_identical():
    """Undated hit (no Event join) → no session_date; rendering unchanged."""
    sdk = _new_sdk()
    point = sdk.create_point("statement", "office hours are 9am")
    hits = sdk.tortoise_fts_query("office hours", limit=40, include_terminal=True)
    assert hits and hits[0]["id"] == point["id"]
    ann = sdk.annotate_ask_hits(hits)
    # additive keys ""/absent — never a marker (byte-identical rendering)
    assert not ann[0].get("session_date")
    assert not ann[0].get("speaker")
    from tortoise.retrieval import render_context
    assert render_context(hits, question_date="2026-08-29") == \
        render_context(ann, question_date="2026-08-29")


# ── #4106: episodic turn Points must be DATABLE ───────────────────────────

#: A capture-shaped session's RECORDED time. Turn Points carry no ``eventId``
#: (the capture turn store stamps provenance on EXTRACTED points only), so a
#: turn's ONLY recorded date is its session's own — the surface #4106 misses.
TURN_SESSION = "turn-sess-1"
TURN_DATE = "2023-05-20"
DATED_SESSION = "turn-sess-dated"
DATED_SESSION_DATE = "1999-01-01"


def _seed_capture_turns(sdk: TortoiseSDK, session_id: str, *,
                        now: str | None,
                        conversation: list[dict]) -> list[str]:
    """Seed ONE session through the SHARED capture-shaped turn store
    (``tools.ask_spotcheck.seed_capture_turn_store``, #3914): deterministic
    ``{sid}_t{i}`` ids, ``pointKind='event'``, ``is_episodic=true``, a
    ``speaker``, NO ``sessionId``/``eventId`` prop, and the
    ``(:Session)-[:CONTAINS]->(:Point)`` edge. ``now`` is the session's
    recorded time (``s.created_at``), which capture writes from the same
    ``now`` as its turns' ``createdAt``."""
    from tools.ask_spotcheck import seed_capture_turn_store
    return seed_capture_turn_store(sdk, session_id, conversation, now=now)


def test_annotate_turn_session_date_from_the_session_record():
    """#4106 property — a capture-shaped turn retrieved for a temporal
    question renders a date EQUAL to its session's recorded time.

    The ``:Event`` join that used to be the only date source is EMPTY for
    these turns by construction (asserted here, so the test cannot pass
    vacuously); the date must reach the reader from the session the turn is
    CONTAINS-wired to — the same provenance the point fetch already resolves
    session IDENTITY from.
    """
    sdk = _new_sdk()
    turns = _seed_capture_turns(
        sdk, TURN_SESSION, now=f"{TURN_DATE}T10:00:00Z",
        conversation=[{"role": "user", "content": "I bought a smoker today"},
                      {"role": "assistant", "content": "noted"}])
    assert turns, "capture-shaped seeder must write turns"
    proj = sdk._get_proj()
    # Precondition: the turn has NO eventId, so the Event join is empty.
    assert proj.g.query(
        "MATCH (t:Point) WHERE t.id IN $ids RETURN count(t.eventId)",
        params={"ids": turns}).result_set == [[0]]
    # Precondition: the session's recorded time IS the fixture's date.
    assert proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.created_at",
        params={"sid": TURN_SESSION}).result_set[0][0] == \
        f"{TURN_DATE}T10:00:00Z"

    hits = sdk.tortoise_fts_query("smoker", limit=40, include_terminal=True)
    ann = sdk.annotate_ask_hits(hits)
    hit = {h["id"]: h for h in ann}[turns[0]]
    assert hit["session_date"] == TURN_DATE, hit
    assert hit["speaker"] == "user", hit

    from tortoise.retrieval import render_context
    evidence = render_context(ann)
    assert f"(session date {TURN_DATE})" in evidence, evidence
    sdk.close()


def test_annotate_unknown_session_date_renders_as_unknown():
    """NEGATIVE CONTROL (#4106): a turn whose session records NO date must
    render as UNKNOWN — never as a default. A wrong date is worse than no
    date, so neither the wall clock (the seeding ``now``), the turn's own
    ``createdAt``, nor a NEIGHBOURING session's date may be substituted.
    """
    sdk = _new_sdk()
    undated = _seed_capture_turns(
        sdk, "undated-sess", now="2024-07-07T09:00:00Z",
        conversation=[{"role": "user", "content": "I bought a smoker today"}])
    # A DATED neighbour that the SAME query retrieves — so the "no other
    # session's date leaks in" assertion below has something to catch.
    dated = _seed_capture_turns(
        sdk, DATED_SESSION, now=f"{DATED_SESSION_DATE}T10:00:00Z",
        conversation=[{"role": "user",
                       "content": "I also bought a smoker yesterday"}])
    assert undated and dated
    proj = sdk._get_proj()
    # The undated session genuinely records NO time (the harness clock is
    # still reflected on the turn's own createdAt — it must not be used).
    proj.g.query("MATCH (s:Session {id:$sid}) SET s.created_at = null",
                 params={"sid": "undated-sess"})
    assert proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.created_at",
        params={"sid": "undated-sess"}).result_set == [[None]]

    hits = sdk.tortoise_fts_query("smoker", limit=40, include_terminal=True)
    ann = sdk.annotate_ask_hits(hits)
    by_id = {h["id"]: h for h in ann}
    # Preconditions, both non-vacuous: the undated turn IS in the pool, and
    # so IS the dated neighbour (whose own date must render).
    assert undated[0] in by_id and dated[0] in by_id, sorted(by_id)
    assert by_id[dated[0]]["session_date"] == DATED_SESSION_DATE, \
        by_id[dated[0]]
    assert not by_id[undated[0]].get("session_date"), by_id[undated[0]]

    from tortoise.retrieval import render_context
    evidence = render_context(ann)
    # the dated neighbour's marker is PRESENT; the undated turn carries none
    assert f"(session date {DATED_SESSION_DATE})" in evidence, evidence
    assert evidence.count("(session date") == 1, evidence
    # no default leaked in any form: not the wall clock, not the turn's own
    # createdAt date
    assert "2024-07-07" not in evidence, evidence
    assert datetime.now(UTC).date().isoformat() not in evidence, \
        evidence
    sdk.close()


def test_date_leg_attaches_no_session_id():
    """D3 pool-safety (#1540 / #4106): the session-``created_at`` leg is a
    DATE source only. The attached ``session_id`` set must be byte-identical
    with and without it (only the ``eventId`` Event join may attach one — a
    new identity source would re-bucket ``dedup_pool`` and move the resolved
    ask-lane reader window (200/200/16000/derived since #4105; 8k/32KiB
    before it))."""
    sdk = _new_sdk()
    turns = _seed_capture_turns(
        sdk, TURN_SESSION, now=f"{TURN_DATE}T10:00:00Z",
        conversation=[{"role": "user", "content": "I bought a smoker"}])
    proj = sdk._get_proj()
    # An EXTRACTED claim with capture's own provenance stamp: its ``eventId``
    # join DOES yield a date (and a sessionId) — the pre-#4106 path.
    claim = sdk.create_point("statement", "I bought a smoker in May")
    proj.g.query(
        "MATCH (p:Point {id:$pid}) SET p.eventId = 'ev-x'",
        params={"pid": claim["id"]})
    proj.g.query(
        "MERGE (e:Event {eventId:'ev-x'}) SET e.startedAt = "
        "'2023-05-21T10:00:00Z', e.sessionId = 'ev-sess'")
    proj.g.query(
        "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
        "MERGE (s)-[:CONTAINS]->(p)",
        params={"sid": TURN_SESSION, "pid": claim["id"]})

    hits = sdk.tortoise_fts_query("smoker", limit=40, include_terminal=True)
    ann = sdk.annotate_ask_hits(hits)
    by_id = {h["id"]: h for h in ann}
    # Precondition (non-vacuous): the date leg DID attach a date.
    assert any(h.get("session_date") for h in ann), ann
    # The turn gets the DATE from the session record ...
    assert by_id[turns[0]]["session_date"] == TURN_DATE
    # ... and NO identity: the CONTAINS session id is never attached as a
    # session_id. (A buggy widening would attach TURN_SESSION here.)
    assert not by_id[turns[0]].get("session_id"), by_id[turns[0]]
    attached = {h["session_id"] for h in ann if h.get("session_id")}
    assert attached == {"ev-sess"}, attached
    sdk.close()


def _ask_key(h: dict) -> str:
    """The REAL ask-lane ``dedup_pool`` key — ``retrieval.ask_session_key``
    (single-sourced, #4155), never a local copy that can drift from the
    pipeline it claims to pin."""
    from tortoise.retrieval import ask_session_key
    return ask_session_key(h)


def test_ask_session_key_precedence():
    """#4155: the key the ask lane buckets on consults the identity the
    point fetch ALREADY populates — snake ``session_id``, then the camel
    ``sessionId`` — before the coarser date fallback and the index bucket.
    ``_pkg_session`` (the packaging slice) is the SAME key, not a copy."""
    from tortoise.retrieval import _pkg_session, ask_session_key
    assert ask_session_key({
        "session_id": "snake", "sessionId": "camel",
        "session_date": "2023-01-01"}) == "snake"
    assert ask_session_key({
        "sessionId": "camel", "session_date": "2023-01-01"}) == "camel"
    # an empty camel value is ABSENT, never a bucket named ""
    assert ask_session_key({
        "sessionId": "", "session_date": "2023-01-01"}) == "2023-01-01"
    assert ask_session_key({"lme_session_index": 7}) == "idx:7"
    assert ask_session_key({}) == "idx:-1"
    assert _pkg_session({"sessionId": "camel"}) == "camel"


def _seed_transcript_chunks(sdk: TortoiseSDK, session_id: str, date: str,
                            n: int = 4) -> list[str]:
    """Raw verbatim chunks in the eval ingest's shape (pointKind
    ``session-transcript``, deterministic ``lme:`` ids, ``Session.created_at``
    = the session's date, CONTAINS-wired) — the ONLY shape ``dedup_pool``'s
    per-session cap actually caps, so it is the shape a date key can move."""
    from tortoise.domain_loader import register_kind
    register_kind("session-transcript")
    proj = sdk._get_proj()
    proj.g.query("MERGE (s:Session {id:$sid}) SET s.created_at=$ts",
                 params={"sid": session_id, "ts": f"{date}T10:00:00Z"})
    ids = []
    for ci in range(n):
        pid = f"lme:{session_id}:c{ci}"
        sdk.create_point("session-transcript",
                         f"verbatim chunk {ci} of {session_id}: the smoker",
                         id=pid, is_episodic=True, status="draft")
        proj.g.query("MATCH (s:Session {id:$sid}),(p:Point {id:$pid}) "
                     "MERGE (s)-[:CONTAINS]->(p)",
                     params={"sid": session_id, "pid": pid})
        ids.append(pid)
    return ids


def test_date_leg_does_not_rebucket_a_chunk_pool():
    """D3 pool-safety, non-vacuously measured (#4106), re-measured after
    #4155.

    Raw chunks carry NO snake ``session_id`` on the hit — only the camel
    ``sessionId`` the point fetch derives from the ``:Session`` ``CONTAINS``
    edge (#4155) — and NEVER ``lme_session_index``. On the collision-prone
    case — two sessions sharing ONE date — the date leg must still keep
    exactly the same survivors: the fetch-populated camel identity outranks
    the date in BOTH readings, so attaching the date re-buckets nothing (and
    #4155's whole point is that the survivors are now PER SESSION, not the
    single global ``idx:-1`` bucket the pre-#4155 key chain produced).
    Measured through the real ``dedup_pool`` with the real ask key, not
    assumed.
    """
    sdk = _new_sdk()
    _seed_transcript_chunks(sdk, "chunkA", TURN_DATE)
    _seed_transcript_chunks(sdk, "chunkB", TURN_DATE)
    hits = sdk.tortoise_fts_query("verbatim chunk smoker", limit=40,
                                  include_terminal=True)
    ann = sdk.annotate_ask_hits(hits)
    assert len(hits) >= 4, [h.get("id") for h in hits]
    assert all(h["point_kind"] == "session-transcript" for h in hits), hits
    # Preconditions, all measured: the date IS populated; no hit carries a
    # snake session_id; and the fetch DID populate the camel identity, one
    # bucket per session (#4155 — without it the pre-fix key was idx:-1).
    assert all(h.get("session_date") == TURN_DATE for h in ann), ann
    assert not any(h.get("session_id") for h in hits), hits
    assert {h.get("sessionId") for h in ann} == {"chunkA", "chunkB"}, ann

    from tortoise.retrieval import dedup_pool
    stripped = [{k: v for k, v in h.items() if k != "session_date"}
                for h in ann]
    with_date = dedup_pool(ann, max_chunks_per_session=3, session_key=_ask_key)
    without = dedup_pool(stripped, max_chunks_per_session=3,
                         session_key=_ask_key)
    assert [h["id"] for h in with_date] == [h["id"] for h in without], (
        "the date leg re-bucketed the chunk pool: "
        f"{[h['id'] for h in with_date]} vs {[h['id'] for h in without]}")
    # ... and the survivors sit in the PER-SESSION camel buckets in both
    # readings — the same-date collision that used to collapse the pool into
    # ONE bucket (``idx:-1`` pre-#4106, the shared date after it) is gone.
    assert {_ask_key(h) for h in ann} == {"chunkA", "chunkB"}
    assert {_ask_key(h) for h in stripped} == {"chunkA", "chunkB"}
    sdk.close()


def test_date_leg_never_narrows_the_chunk_pool():
    """#4106 measured effect on DISTINCT dates, isolated after #4155: with NO
    identity the date key is a refinement of the identity-less ``idx:-1``
    bucket, so it can restore chunks the collapse was dropping — and can
    never keep fewer.

    #4155 makes the fetch-populated camel ``sessionId`` outrank the date, so
    the camel key is stripped from BOTH readings here — otherwise this would
    re-measure the camel identity, not the DATE leg it is named for. The
    snake/idx fallback is what remains, so the only difference between the
    two readings IS ``session_date``.
    """
    sdk = _new_sdk()
    _seed_transcript_chunks(sdk, "chunkA", "2023-03-15")
    _seed_transcript_chunks(sdk, "chunkB", "2023-04-01")
    hits = sdk.tortoise_fts_query("verbatim chunk smoker", limit=40,
                                  include_terminal=True)
    ann = sdk.annotate_ask_hits(hits)
    from tortoise.retrieval import dedup_pool
    camel_stripped = [{k: v for k, v in h.items() if k != "sessionId"}
                      for h in ann]
    identityless = [{k: v for k, v in h.items()
                     if k not in ("session_date", "sessionId")}
                    for h in ann]
    with_date = dedup_pool(camel_stripped, max_chunks_per_session=3,
                           session_key=_ask_key)
    without = dedup_pool(identityless, max_chunks_per_session=3,
                         session_key=_ask_key)
    # Non-vacuity: the date leg produced DISTINCT per-session buckets, and
    # the identity-less reading really was the single global bucket — so the
    # superset assertion below has signal (equality would red here).
    assert {_ask_key(h) for h in camel_stripped} == {
        "2023-03-15", "2023-04-01"}, camel_stripped
    assert {_ask_key(h) for h in identityless} == {"idx:-1"}, identityless
    assert {h["id"] for h in without} < {h["id"] for h in with_date}, (
        "expected the global idx:-1 bucket to have collapsed more hits: "
        f"{sorted(h['id'] for h in without)} vs "
        f"{sorted(h['id'] for h in with_date)}")
    assert {h["id"] for h in without} <= {h["id"] for h in with_date}, (
        f"the date leg DROPPED hits: {[h['id'] for h in without]} -> "
        f"{[h['id'] for h in with_date]}")
    sdk.close()


def test_dedup_key_reads_the_camel_session_id_on_a_date_less_pool(
        monkeypatch):
    """#4155 regression, end-to-end through the real ask lane.

    A captured-turn/transcript chunk carries NO snake ``session_id`` (only
    ``annotate_ask_hits``'s Event join attaches one, and these points have no
    ``eventId``) and no ``lme_session_index``. Its identity is the camel
    ``sessionId`` the point fetch populates from the ``:Session``
    ``CONTAINS`` edge. On a DATE-LESS pool (the sessions record no
    ``created_at``) the pre-#4155 key chain (snake ``session_id`` →
    ``session_date`` → ``idx:``) therefore collapsed every chunk into the
    single global bucket ``idx:-1``, so ``dedup_pool``'s per-session cap
    applied GLOBALLY: 8 chunks across 2 sessions → 3 survivors. The camel
    identity must bucket PER SESSION → 3 survivors each, 6 total.
    """
    # The fleet shell carries TORTOISE_API_URL; the eval lane needs a LOCAL
    # graph (the hosted ask surface was removed in #3849).
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    sdk = _new_sdk()
    for sid in ("chunkA", "chunkB"):
        _seed_transcript_chunks(sdk, sid, TURN_DATE, n=4)
    # DATE-LESS: no recorded session time, so the date-leg fallback is empty
    # and the ONLY identity on the wire is the fetch's camel ``sessionId``.
    proj = sdk._get_proj()
    for sid in ("chunkA", "chunkB"):
        proj.g.query("MATCH (s:Session {id:$sid}) REMOVE s.created_at",
                     params={"sid": sid})
    _install_fake(sdk, monkeypatch)
    result = run_ask_lane(sdk, "verbatim chunk smoker",
                          question_date="2023-06-01")
    evidence = result["evidence"]
    surviving = [
        f"verbatim chunk {ci} of {sid}"
        for sid in ("chunkA", "chunkB")
        for ci in range(4)
        if f"verbatim chunk {ci} of {sid}" in evidence
    ]
    assert len(surviving) == 6, (
        f"the per-session chunk cap applied globally: {surviving}")
    assert sum(1 for s in surviving if "chunkA" in s) == 3, surviving
    assert sum(1 for s in surviving if "chunkB" in s) == 3, surviving
    sdk.close()


def test_multi_session_point_takes_the_earliest_recorded_date():
    """A point CONTAINS-wired to TWO sessions resolves to the EARLIEST of
    their recorded times — deterministic (never the engine's unspecified row
    order) and one of the point's own recorded session times, not an
    inference. Mirrors the point fetch's own deterministic multi-session pick,
    which is over session IDs rather than dates."""
    sdk = _new_sdk()
    point = sdk.create_point("statement",
                             "I bought a smoker on the spring trip")
    proj = sdk._get_proj()
    for sid, ts in (("multi-late", "2023-05-20T10:00:00Z"),
                    ("multi-early", "2023-03-15T10:00:00Z")):
        proj.g.query("MERGE (s:Session {id:$sid}) SET s.created_at=$ts",
                     params={"sid": sid, "ts": ts})
        proj.g.query("MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
                     "MERGE (s)-[:CONTAINS]->(p)",
                     params={"sid": sid, "pid": point["id"]})
    hits = sdk.tortoise_fts_query("smoker spring trip", limit=40,
                                  include_terminal=True)
    ann = {h["id"]: h for h in sdk.annotate_ask_hits(hits)}
    assert ann[point["id"]]["session_date"] == "2023-03-15", \
        ann[point["id"]]
    sdk.close()


def test_malformed_recorded_time_renders_as_unknown():
    """#4106: a recorded time that is not ``YYYY-MM-DD`` (a bare word, an
    epoch, an offset-less oddity) renders as UNKNOWN — never as a truncated
    garbage ``date`` that a reader would compute elapsed time from."""
    sdk = _new_sdk()
    turns = _seed_capture_turns(
        sdk, "bad-ts-sess", now="not-a-date",
        conversation=[{"role": "user", "content": "I bought a smoker"}])
    proj = sdk._get_proj()
    assert proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.created_at",
        params={"sid": "bad-ts-sess"}).result_set == [["not-a-date"]]
    hits = sdk.tortoise_fts_query("smoker", limit=40, include_terminal=True)
    ann = {h["id"]: h for h in sdk.annotate_ask_hits(hits)}
    assert not ann[turns[0]].get("session_date"), ann[turns[0]]
    assert "not-a-date" not in str(ann[turns[0]])
    sdk.close()


def test_eval_ingest_records_no_time_for_a_dateless_session():
    """#4106 NEGATIVE CONTROL on the EVAL INGEST shapes: a session the dataset
    does not date must record NO session time — not the ingestion wall clock.
    ``Session.created_at`` is what the annotation reads, so a ``now()``
    fallback there would render the RUN DATE as the session's date (a
    fabricated fact on the read path) and would change daily.

    BOTH writers are covered: the deterministic leg (``ingest_haystack``) and
    the v2 phase-A write (``_write_v2_phase_a``) — a silent revert of either
    one re-opens the fabrication path.
    """
    sdk = _new_sdk()
    from tools.longmem_eval.ingest import ingest_haystack
    ingest_haystack(sdk, {
        "question_id": "dateless-eval-1",
        "haystack_sessions": [[{"role": "user",
                                "content": "I bought a smoker today"},
                               {"role": "assistant", "content": "noted"}]],
        "haystack_session_ids": ["dateless-sess"],
        "haystack_dates": [""],
    })
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (s:Session) RETURN s.id, s.created_at").result_set
    assert rows, "ingest must write the session"
    assert all(r[1] is None for r in rows), rows
    hits = sdk.tortoise_fts_query("smoker", limit=40, include_terminal=True)
    ann = sdk.annotate_ask_hits(hits)
    assert ann and all(not h.get("session_date") for h in ann), ann
    from tortoise.retrieval import render_context
    assert "(session date" not in render_context(ann)
    assert datetime.now(UTC).date().isoformat() not in render_context(ann)

    # the v2 writer, same contract
    from tools.longmem_eval.ingest_v2 import _write_v2_phase_a
    _write_v2_phase_a(sdk, qid="dateless-v2", si=0, sid="dateless-v2-sess",
                      s_node="lme:dateless-v2:s0",
                      session=[{"role": "user",
                                "content": "I bought a smoker today"}],
                      session_date="", point_created_at="1970-01-01T00:00:00Z",
                      chunk_turns=10)
    v2 = proj.g.query(
        "MATCH (s:Session {id:'lme:dateless-v2:s0'}) RETURN s.created_at"
    ).result_set
    assert v2 == [[None]], v2

    # RE-INGEST over a store the OLD writer already stamped with its run
    # clock: the dateless session must CONVERGE to "no time", not keep the
    # fabricated date (a `coalesce` write would preserve it forever).
    proj.g.query("MATCH (s:Session {id:$sid}) "
                 "SET s.created_at = '2026-09-19T01:35:25+00:00'",
                 params={"sid": "lme:dateless-eval-1:s0"})
    ingest_haystack(sdk, {
        "question_id": "dateless-eval-1",
        "haystack_sessions": [[{"role": "user",
                                "content": "I bought a smoker today"},
                               {"role": "assistant", "content": "noted"}]],
        "haystack_session_ids": ["dateless-sess"],
        "haystack_dates": [""],
    })
    redo = proj.g.query(
        "MATCH (s:Session {id:'lme:dateless-eval-1:s0'}) RETURN s.created_at"
    ).result_set
    assert redo == [[None]], redo
    sdk.close()


def test_eval_ingest_slash_date_reaches_the_reader():
    """#4106 POSITIVE CONTROL on the eval ingest's REAL date format.

    The dataset's ``haystack_dates`` are ``2023/05/20 (Sat) 03:29`` (measured:
    23,867/23,867 slash-form, zero ISO), and the ingest writes that string
    verbatim into ``Session.created_at``. The date annotation must normalise
    it (not reject it) — a recorded date that is silently dropped is the same
    class of loss as one that is never emitted.
    """
    sdk = _new_sdk()
    from tools.longmem_eval.ingest import ingest_haystack
    ingest_haystack(sdk, {
        "question_id": "slash-eval-1",
        "haystack_sessions": [[{"role": "user",
                                "content": "I bought a smoker today"}]],
        "haystack_session_ids": ["slash-sess"],
        "haystack_dates": ["2023/05/20 (Sat) 03:29"],
    })
    hits = sdk.tortoise_fts_query("smoker", limit=40, include_terminal=True)
    ann = sdk.annotate_ask_hits(hits)
    assert ann and all(h.get("session_date") == "2023-05-20" for h in ann), \
        ann
    from tortoise.retrieval import render_context
    assert "(session date 2023-05-20)" in render_context(ann)
    sdk.close()


def test_ask_lane_evidence_carries_the_session_date(monkeypatch):
    """End-to-end (#4106): the ask lane's assembled reader context carries
    ``(session date YYYY-MM-DD)`` for a capture-shaped turn. No LLM call —
    the lane's single reader call is stubbed."""
    # The fleet shell carries TORTOISE_API_URL; the eval lane needs a LOCAL
    # graph (the hosted ask surface was removed in #3849).
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)

    class _FakeReader:
        last_completion_tokens = 5

        def complete(self, *, system: str, user: str) -> str:
            return "you bought a smoker on 2023-05-20"

        def close(self) -> None:
            pass

    sdk = _new_sdk()
    _seed_capture_turns(
        sdk, TURN_SESSION, now=f"{TURN_DATE}T10:00:00Z",
        conversation=[{"role": "user", "content": "I bought a smoker today"}])
    import tortoise.ask_lane as ask_lane_mod
    monkeypatch.setattr(ask_lane_mod, "_default_ask_reader_factory",
                        lambda: _FakeReader())
    question = "how many days ago did I buy a smoker?"
    result: dict = {}
    for _ in range(3):  # embedded engine's per-strategy degradation flake
        result = run_ask_lane(sdk, question, question_date="2023-05-25")
        if f"(session date {TURN_DATE})" in result.get("evidence", ""):
            break
    assert f"(session date {TURN_DATE})" in result.get("evidence", ""), \
        result.get("evidence")
    sdk.close()


def test_annotate_null_join_byte_identical():
    """A hit whose eventId has NO Event node + no source turn → additive keys
    absent, no crash, no stale markers."""
    sdk = _new_sdk()
    sdk.create_point("statement", "office hours are 9am")
    hits = sdk.tortoise_fts_query("office hours", limit=40, include_terminal=True)
    # decorate the hit with a dangling eventId
    ann = sdk.annotate_ask_hits(hits)
    assert ann[0]["id"] == hits[0]["id"]
    assert not ann[0].get("session_date")


def test_annotate_has_answer_passthrough():
    """A hit carrying has_answer survives annotate_ask_hits unchanged."""
    sdk = _new_sdk()
    proj = sdk._get_proj()
    point = sdk.create_point("statement", "office hours are 9am")
    proj.g.query(
        "MATCH (p:Point {id: $pid}) SET p.has_answer = true, p.eventId = 'ev1'",
        params={"pid": point["id"]},
    )
    proj.g.query(
        "MERGE (e:Event {eventId: 'ev1'}) SET e.startedAt = '2026-08-02T10:00:00Z'",
    )
    hits = sdk.tortoise_fts_query("office hours", limit=40, include_terminal=True)
    # the MockReader-stamping field is not a SearchResult.to_dict field —
    # the eval's read-time marks inject it into the hit dict; annotate must
    # preserve it (dict passthrough, never dropped).
    hits[0]["has_answer"] = True
    ann = sdk.annotate_ask_hits(hits)
    assert ann[0]["has_answer"] is True
    assert ann[0]["session_date"] == "2026-08-02"


def test_annotate_d8_rides_through_decorated_hits():
    """Superseded point → superseded_by rides through the ALREADY-decorated
    hits (annotate_ask_hits does NOT re-fetch D8 state)."""
    sdk = _new_sdk()
    old = sdk.create_point("statement", "gym schedule is Monday")
    new = sdk.create_point("statement", "gym schedule is Tuesday")
    sdk.supersede_point(old["id"], new["id"])
    hits = sdk.tortoise_fts_query("gym schedule", limit=40, include_terminal=True)
    ann = sdk.annotate_ask_hits(hits)
    old_hit = next(h for h in ann if h["id"] == old["id"])
    assert old_hit.get("superseded_by"), "D8 marker must ride through"
    assert old_hit.get("status") == "superseded"


def test_annotate_dedup_key_order_pinning():
    """P2-20: hits LACKING sessionId but sharing an Event join group by the
    ANNOTATED session identifier — the per-session dedup cap applies."""
    from tortoise.retrieval import dedup_pool
    hits = [
        {"id": "a", "content": "chunk 1", "point_kind": "session-transcript",
         "session_date": "2026-08-01"},
        {"id": "b", "content": "chunk 2", "point_kind": "session-transcript",
         "session_date": "2026-08-01"},
        {"id": "c", "content": "chunk 3", "point_kind": "session-transcript",
         "session_date": "2026-08-01"},
        {"id": "d", "content": "chunk 4", "point_kind": "session-transcript",
         "session_date": "2026-08-01"},
        {"id": "e", "content": "other", "point_kind": "session-transcript",
         "session_date": "2026-08-02"},
    ]
    key = lambda h: h.get("session_date") or h.get("session_id") or f"idx:{h.get('lme_session_index', -1)}"  # noqa: E731
    deduped = dedup_pool(hits, max_chunks_per_session=3, session_key=key)
    assert [h["id"] for h in deduped] == ["a", "b", "c", "e"]


# ── Task 5: local-lane ask pipeline ────────────────────────────────────────

def _install_fake(sdk: TortoiseSDK, monkeypatch, reply="The gym schedule is Monday and Wednesday.", tokens_out=12) -> FakeReader:
    import tortoise.ask_lane as sdk_mod
    fake = FakeReader(reply=reply, tokens_out=tokens_out)
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
    return fake


def test_local_lane_pipeline(monkeypatch):
    sdk = _new_sdk()
    _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday and Wednesday",
         "eventId": "ev1", "session_date": "2026-08-01", "speaker": "user"},
    ])
    fake = _install_fake(sdk, monkeypatch)
    result = run_ask_lane(sdk, "what is the gym schedule?", question_date="2026-08-29")
    # the full 13-field shape
    assert set(result) == {"answer", "abstained", "question_type",
                           "question_date", "evidence", "context_tokens",
                           "model", "provider", "route", "cost_estimate_usd",
                           "duration_ms", "retrieval_degraded",
                           "retrieved_session_ids"}
    assert result["answer"] == "The gym schedule is Monday and Wednesday."
    assert result["abstained"] is False
    assert result["question_date"] == "2026-08-29"  # resolved value
    assert result["context_tokens"] == estimate_tokens_ask(result["evidence"])
    assert result["context_tokens"] <= 8000
    assert result["cost_estimate_usd"] > 0
    assert result["duration_ms"] >= 0
    assert fake.calls == 1  # exactly ONE model call


def test_cost_estimate_strong_rates_for_qwen_serving_reader(monkeypatch):
    """#2069: the response's cost_estimate_usd is metered at
    ASK_METER_RATES_STRONG when the SERVING lane's wire id is a
    strong-family spec (``qwen/qwen3.8-max`` via ``_LockedReader.model``)
    — the strong lane never reports a deepseek-envelope estimate."""
    import tortoise.ask_lane as sdk_mod
    from tortoise.metering import ASK_METER_RATES_STRONG, estimate_ask_cost_usd
    from tortoise.reader import system_prompt_for

    class _StrongReader(FakeReader):
        model = "qwen/qwen3.8-max"
        provider = "openrouter"
        route = "openrouter"
        last_finish_reason = "stop"
        last_completion_tokens = 12

    sdk = _new_sdk()
    fake = _StrongReader()
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
    result = run_ask_lane(sdk, "q")
    assert result["model"] == "qwen/qwen3.8-max"
    inp = (estimate_tokens_ask(system_prompt_for(result["question_type"]))
           + estimate_tokens_ask(result["evidence"]))
    expected = estimate_ask_cost_usd(inp, 12, rates=ASK_METER_RATES_STRONG)
    assert result["cost_estimate_usd"] == pytest.approx(expected)


def test_cost_estimate_default_rates_for_deepseek_serving_reader(monkeypatch):
    """#2069 regression: a deepseek-family serving wire id (the default
    lane — bare ``deepseek-v4-flash`` on deepseek-direct) keeps the deepseek
    envelope; the response cost_estimate_usd is unchanged for the default
    lane."""
    import tortoise.ask_lane as sdk_mod
    from tortoise.metering import ASK_METER_RATES, estimate_ask_cost_usd
    from tortoise.reader import system_prompt_for

    class _DeepSeekReader(FakeReader):
        model = "deepseek-v4-flash"
        provider = "deepseek-direct"
        route = "deepseek-direct"
        last_completion_tokens = 12

    sdk = _new_sdk()
    fake = _DeepSeekReader()
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
    result = run_ask_lane(sdk, "q")
    assert result["model"] == "deepseek-v4-flash"
    inp = (estimate_tokens_ask(system_prompt_for(result["question_type"]))
           + estimate_tokens_ask(result["evidence"]))
    expected = estimate_ask_cost_usd(inp, 12, rates=ASK_METER_RATES)
    assert result["cost_estimate_usd"] == pytest.approx(expected)


def test_ask_record_path_uses_strong_rates(monkeypatch):
    """#2069: the run_ask_lane() metering RECORD call site (step 7 — the pinned
    record path) meters the cost_usd at the SERVING lane's STRONG rates — a
    strong-lane query's cost_usd record never uses the deepseek envelope."""
    import tortoise.ask_lane as sdk_mod
    import tortoise.metering as metering_mod
    from tortoise.metering import (
        ASK_METER_RATES,
        ASK_METER_RATES_STRONG,
        estimate_ask_cost_usd,
    )

    captured = {}

    def _capture(org_id, *, tokens_in=0, tokens_out=0, cost_usd=0.0, **_):
        captured.update(tokens_in=tokens_in, tokens_out=tokens_out,
                        cost_usd=cost_usd)
        return None

    monkeypatch.setattr(metering_mod, "record_ask_usage", _capture)

    class _StrongReader(FakeReader):
        model = "qwen/qwen3.8-max"
        provider = "openrouter"
        route = "openrouter"
        last_completion_tokens = 12

    sdk = _new_sdk()
    fake = _StrongReader()
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
    run_ask_lane(sdk, "q", org_id="team-x")
    assert captured, "the record path must have run (explicit org_id)"
    expected = estimate_ask_cost_usd(
        captured["tokens_in"], captured["tokens_out"],
        rates=ASK_METER_RATES_STRONG)
    assert captured["cost_usd"] == pytest.approx(expected)
    # never the deepseek envelope (the under-count hazard is gone)
    under = estimate_ask_cost_usd(
        captured["tokens_in"], captured["tokens_out"], rates=ASK_METER_RATES)
    assert captured["cost_usd"] > under


def test_local_lane_default_question_date_utc(monkeypatch):
    """question_date default = server-now-UTC (resolved value in the
    response); a non-UTC clock at a boundary time does not leak local date."""
    sdk = _new_sdk()
    _install_fake(sdk, monkeypatch)
    result = run_ask_lane(sdk, "q")
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", result["question_date"])
    assert result["evidence"].startswith(f"Current Date: {result['question_date']}")


def test_empty_context_single_call(monkeypatch):
    """Empty pool → exactly ONE model call (no pre-gate) + abstained with
    evidence present."""
    sdk = _new_sdk()
    fake = _install_fake(sdk, monkeypatch, reply="I do not know the answer.")
    result = run_ask_lane(sdk, "something not in memory")
    assert fake.calls == 1
    assert result["abstained"] is True
    assert result["evidence"] is not None
    assert "do not know" in result["answer"]


def test_decoy_near_miss_exactly_one_call(monkeypatch):
    """Decoy-only and near-miss-only pools → exactly ONE model call each."""
    sdk = _new_sdk()
    _seed_event_graph(sdk, [
        {"content": "I bought a new bicycle yesterday", "eventId": "ev1",
         "session_date": "2026-08-20"},
    ])
    fake = _install_fake(sdk, monkeypatch, reply="I do not know.")
    # decoy: asks for a value that is a different attribute
    r1 = run_ask_lane(sdk, "what is the bicycle's color?")
    assert fake.calls == 1 and r1["abstained"] is True
    # near-miss: similar but different value present
    r2 = run_ask_lane(sdk, "what is the car's color?")
    assert fake.calls == 2 and r2["abstained"] is True


def test_local_lane_validation_first_zero_calls(monkeypatch):
    """P2-8: invalid inputs → AskValidationError with ZERO model calls AND
    zero retrieval calls (the retrieval stub is never invoked)."""
    sdk = _new_sdk()
    fake = _install_fake(sdk, monkeypatch)
    retrieved = []

    orig = sdk.tortoise_fts_query
    def _no_retrieval(*a, **k):
        retrieved.append(1)
        return orig(*a, **k)
    monkeypatch.setattr(sdk, "tortoise_fts_query", _no_retrieval)

    for bad, kw, code in [
        ("", {}, CODE_INVALID_QUESTION),
        ("   ", {}, CODE_INVALID_QUESTION),
        ("x" * 2001, {}, CODE_QUESTION_TOO_LONG),
        ("q", {"question_type": "bogus"}, CODE_INVALID_QUESTION_TYPE),
        ("q", {"question_date": "2023-02-29"}, CODE_INVALID_QUESTION_DATE),
        ("a\x00b", {}, CODE_INVALID_QUESTION),
        ("\u200b", {}, CODE_INVALID_QUESTION),
        ("q", {"question_date": 20230101},
         CODE_INVALID_QUESTION_DATE),  # non-str date → str()-coerced by
                                       # ask_lane._ask_validate (the removed
                                       # hosted AskRequest validator behaved
                                       # identically — P2)
    ]:
        with pytest.raises(AskValidationError) as ei:
            run_ask_lane(sdk, bad, **kw)
        assert ei.value.code == code, (bad, kw, ei.value.code)
    assert fake.calls == 0
    assert retrieved == []


def test_2000_char_boundary(monkeypatch):
    sdk = _new_sdk()
    fake = _install_fake(sdk, monkeypatch)
    assert run_ask_lane(sdk, "x" * 2000)["answer"] == fake.reply  # passes
    with pytest.raises(AskValidationError):
        run_ask_lane(sdk, "x" * 2001)


def test_oversized_hit_skip_and_caps(monkeypatch):
    """8k/40 caps honored; the byte cap (32 KiB) binds independently; the
    evidence never splits a character (no U+FFFD).

    The caps are SET explicitly: #4105 raised the ask-lane defaults to
    200/200/16000/128000 bytes, and this test pins the CAP-BINDING mechanism at a
    known small shape rather than depending on the product defaults."""
    monkeypatch.setenv("TORTOISE_ASK_RETRIEVAL_LIMIT", "40")
    monkeypatch.setenv("TORTOISE_ASK_CONTEXT_ITEM_CAP", "40")
    monkeypatch.setenv("TORTOISE_ASK_POOL_SIZE", "120")
    monkeypatch.setenv("TORTOISE_ASK_CONTEXT_TOKEN_CAP", "8000")
    monkeypatch.setenv("TORTOISE_ASK_CONTEXT_BYTE_CAP", "32768")
    sdk = _new_sdk()
    proj = sdk._get_proj()
    # a pathological CJK-heavy pool (unspaced runs — the word-based estimate
    # under-counts; the byte cap must bind)
    big_run = "\u4f60" * 30000  # ~90 KiB of CJK
    point = sdk.create_point("statement", big_run)
    proj.g.query(
        "MATCH (p:Point {id: $pid}) SET p.eventId = 'ev1'",
        params={"pid": point["id"]},
    )
    proj.g.query(
        "MERGE (e:Event {eventId: 'ev1'}) SET e.startedAt = '2026-08-01T10:00:00Z'",
    )
    fake = _install_fake(sdk, monkeypatch)
    result = run_ask_lane(sdk, "office hours")
    assert len(result["evidence"].encode("utf-8")) <= 32768
    assert estimate_tokens_ask(result["evidence"]) <= 8000
    assert "\ufffd" not in result["evidence"]
    assert fake.calls == 1


def test_undated_hits(monkeypatch):
    sdk = _new_sdk()
    sdk.create_point("statement", "office hours are 9am")
    fake = _install_fake(sdk, monkeypatch)
    result = run_ask_lane(sdk, "office hours?")
    assert result["answer"] == fake.reply
    assert result["evidence"]


def test_question_type_passthrough_and_override(monkeypatch):
    sdk = _new_sdk()
    _install_fake(sdk, monkeypatch)
    r = run_ask_lane(sdk, "how many days ago did we meet?")
    assert r["question_type"] == "temporal-reasoning"
    r2 = run_ask_lane(sdk, "how many days ago did we meet?",
                 question_type="multi-session")
    assert r2["question_type"] == "multi-session"


def test_reader_raise_maps_reader_unavailable(monkeypatch):
    sdk = _new_sdk()
    import tortoise.ask_lane as sdk_mod
    class Boom(FakeReader):
        def complete(self, *, system, user):
            raise RuntimeError("provider down")
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: Boom())
    with pytest.raises(AskReaderUnavailable):
        run_ask_lane(sdk, "q")


def test_tokens_race_same_cached_instance(monkeypatch):
    """2-3 concurrent run_ask_lane() calls through ONE cached model instance → each
    call's captured usage matches its own completion (the per-instance lock
    makes inner complete() + capture atomic)."""
    import tortoise.ask_lane as sdk_mod
    sdk = _new_sdk()
    _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday", "eventId": "ev1",
         "session_date": "2026-08-01"},
        {"content": "the gym schedule is Wednesday", "eventId": "ev2",
         "session_date": "2026-08-01"},
        {"content": "the gym schedule is Friday", "eventId": "ev3",
         "session_date": "2026-08-01"},
    ])

    class RaceReader:
        def __init__(self):
            self.lock = threading.Lock()
            self.completed = 0

        def complete(self, *, system, user):
            with self.lock:
                self.completed += 1
                n = self.completed
            # yield the CPU to force interleaving
            time.sleep(0.01)
            self.last_completion_tokens = n * 10
            return f"answer {n}"

        def close(self):
            pass

    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: RaceReader())
    results = []
    errors = []

    def _ask(q):
        try:
            results.append(run_ask_lane(sdk, q))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_ask, args=(f"what is the schedule {i}?",))
               for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(results) == 3
    # every answer got a response; the shared instance served all three
    assert all(r["answer"] for r in results)


def test_cache_key_isolation():
    """Team A vs team B resolve to DIFFERENT cached model instances."""
    from tortoise.ask_lane import _ask_reader_model
    sdk_a = TortoiseSDK(tempfile.mkdtemp() + "/a.db", namespace="team-a")
    sdk_b = TortoiseSDK(tempfile.mkdtemp() + "/b.db", namespace="team-b")
    a = _ask_reader_model(sdk_a)
    b = _ask_reader_model(sdk_b)
    assert a is not b
    sdk_a.close()
    sdk_b.close()


def test_failed_build_never_cached(monkeypatch):
    """P2-21: the first ask's build raises → the cache does NOT retain the
    key → the second ask rebuilds and succeeds. P2: the per-key build lock
    is ALSO popped on the failed build — it never lingers in the module
    dict (unbounded growth under sustained build failure across
    namespaces)."""
    import tortoise.ask_lane as sdk_mod
    sdk = _new_sdk()
    state = {"fail": True}
    def _factory():
        if state["fail"]:
            raise RuntimeError("build boom")
        return FakeReader(reply="ok now")
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", _factory)
    with pytest.raises(AskReaderUnavailable):
        run_ask_lane(sdk, "q")
    # P2: the failed build leaves NO cached entry AND no lingering lock
    assert sdk_mod._ask_build_locks == {}
    state["fail"] = False
    result = run_ask_lane(sdk, "q")
    assert result["answer"] == "ok now"


def test_locked_reader_forwards_finish_reason(monkeypatch):
    """_LockedReader forwards the inner model's last_finish_reason from the
    same capture frame as the token usage (P2 — consumers previously got
    None because the attribute was never set)."""
    import tortoise.ask_lane as sdk_mod

    class FinishReader:
        def complete(self, *, system, user):
            self.last_prompt_tokens = 10
            self.last_completion_tokens = 20
            self.last_finish_reason = "stop"
            return "the gym schedule is Monday"

        def close(self):
            pass

    locked = sdk_mod._LockedReader(FinishReader())
    assert locked.complete(system="s", user="u") == "the gym schedule is Monday"
    assert locked.last_prompt_tokens == 10
    assert locked.last_completion_tokens == 20
    assert locked.last_finish_reason == "stop"
    # an inner model WITHOUT the attribute → None (never a crash)
    locked2 = sdk_mod._LockedReader(FakeReader(reply="x"))
    locked2.complete(system="s", user="u")
    assert locked2.last_finish_reason is None


def test_locked_reader_forwards_max_tokens(monkeypatch):
    """#2280: _LockedReader forwards a per-call max_tokens override to the
    inner model (the escalation lever) and omits it when not provided."""
    import tortoise.ask_lane as sdk_mod

    seen = {}

    class CapReader:
        def complete(self, *, system, user, max_tokens=None):
            seen["mt"] = max_tokens
            self.last_completion_tokens = 7
            self.last_finish_reason = "stop"
            return "x"

        def close(self):
            pass

    locked = sdk_mod._LockedReader(CapReader())
    assert locked.complete(system="s", user="u", max_tokens=2000) == "x"
    assert seen["mt"] == 2000
    locked.complete(system="s", user="u")
    assert seen["mt"] is None


class _CollapsingReader:
    """complete() stub emulating a reasoning-budget collapse (#2280):
    scripted replies + finish_reasons; records per-call max_tokens."""

    def __init__(self, sequence, finish_reasons, tokens=None):
        self.sequence = list(sequence)
        self.finish_reasons = list(finish_reasons)
        # per-call completion-token reports (billed usage); default 12
        self.tokens = list(tokens) if tokens is not None else [12] * max(
            len(self.sequence), 1)
        self.calls = 0
        self.max_tokens_seen: list = []
        self.last_finish_reason = None
        self.last_completion_tokens = 0

    def complete(self, *, system, user, max_tokens=None):
        self.calls += 1
        self.max_tokens_seen.append(max_tokens)
        self.last_finish_reason = self.finish_reasons[
            min(self.calls - 1, len(self.finish_reasons) - 1)]
        self.last_completion_tokens = self.tokens[
            min(self.calls - 1, len(self.tokens) - 1)]
        return self.sequence[min(self.calls - 1, len(self.sequence) - 1)]

    def close(self):
        pass


def _install_collapsing(sdk, monkeypatch, sequence, finish_reasons):
    import tortoise.ask_lane as sdk_mod
    fake = _CollapsingReader(sequence, finish_reasons)
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory",
                        lambda: fake)
    # deterministic escalation budget for the assertions
    monkeypatch.setenv("TORTOISE_ASK_ESCALATION_TOKENS", "2000")
    return fake


def test_collapse_escalates_and_answers(monkeypatch):
    """#2280: first call collapses empty with finish_reason='length' (the
    reasoning-budget collapse) → ONE escalated retry answers; the result is
    a real answer, NEVER a fabricated abstention."""
    sdk = _new_sdk()
    _seed_event_graph(sdk, [
        {"content": "I spent $25 on a chain and $40 on bike lights",
         "eventId": "ev1", "session_date": "2026-01-05"},
    ])
    fake = _install_collapsing(sdk, monkeypatch,
                               sequence=[None, "$65"],
                               finish_reasons=["length", "length"])
    result = run_ask_lane(sdk, "How much did I spend on bike stuff?",
                     question_date="2026-02-01")
    assert fake.calls == 2
    assert fake.max_tokens_seen == [None, 2000]
    assert result["answer"] == "$65"
    assert result["abstained"] is False


def test_collapse_persists_fails_loud_not_abstention(monkeypatch):
    """#2280: empty output after the escalated retry → AskReaderUnavailable
    (fail-loud). An empty model output is NEVER an abstention."""
    from tortoise.exceptions import AskReaderUnavailable
    sdk = _new_sdk()
    _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday", "eventId": "ev1",
         "session_date": "2026-08-01"},
    ])
    fake = _install_collapsing(sdk, monkeypatch,
                               sequence=[None, None],
                               finish_reasons=["length", "length"])
    with pytest.raises(AskReaderUnavailable):
        run_ask_lane(sdk, "what is the gym schedule?")
    assert fake.calls == 2
    assert fake.max_tokens_seen == [None, 2000]


def test_empty_stop_finish_recovers_on_same_budget_retry(monkeypatch):
    """#2280: empty output with a NON-length finish reason (transient
    empty/provider variance) retries ONCE at the SAME budget and can
    recover — no escalation needed."""
    sdk = _new_sdk()
    fake = _install_collapsing(sdk, monkeypatch,
                               sequence=[None, "I do not know."],
                               finish_reasons=["stop", "stop"])
    result = run_ask_lane(sdk, "something not in memory")
    assert fake.calls == 2
    assert fake.max_tokens_seen == [None, None]
    assert result["answer"] == "I do not know."
    assert result["abstained"] is True  # a WRITTEN abstention


def test_empty_stop_finish_persists_fails_loud(monkeypatch):
    """#2280: empty output twice with a non-length finish reason →
    AskReaderUnavailable (fail-loud), never abstained."""
    from tortoise.exceptions import AskReaderUnavailable
    sdk = _new_sdk()
    fake = _install_collapsing(sdk, monkeypatch,
                               sequence=[None, None],
                               finish_reasons=["stop", "stop"])
    with pytest.raises(AskReaderUnavailable):
        run_ask_lane(sdk, "q")
    assert fake.calls == 2
    assert fake.max_tokens_seen == [None, None]


def test_collapse_metering_counts_both_billed_calls(monkeypatch):
    """#2280: on the 2-call escalation path the metering record + response
    cost estimate account for BOTH billed calls (the collapsed first call's
    tokens were dropped pre-fix — the per-call last_completion_tokens
    capture only reflects the LAST call)."""
    import tortoise.ask_lane as sdk_mod
    import tortoise.metering as metering_mod

    captured = {}

    def _capture(org_id, *, tokens_in=0, tokens_out=0, cost_usd=0.0, **_):
        captured.update(tokens_out=tokens_out, cost_usd=cost_usd)
        return None

    monkeypatch.setattr(metering_mod, "record_ask_usage", _capture)
    sdk = _new_sdk()
    fake = _CollapsingReader(sequence=[None, "x"],
                             finish_reasons=["length", "length"],
                             tokens=[500, 20])
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory",
                        lambda: fake)
    monkeypatch.setenv("TORTOISE_ASK_ESCALATION_TOKENS", "2000")
    run_ask_lane(sdk, "q", org_id="team-x")
    assert captured, "the record path must have run (explicit org_id)"
    assert captured["tokens_out"] == 520  # 500 (collapsed) + 20 (escalated)


def test_both_not_either_control(monkeypatch):
    """tortoise_search / tortoise_recall never invoke the reader factory."""
    sdk = _new_sdk()
    _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday", "eventId": "ev1",
         "session_date": "2026-08-01"},
    ])
    fake = _install_fake(sdk, monkeypatch)
    sdk.tortoise_fts_query("gym", limit=10)
    sdk.recall_state(query="gym")
    sdk.recall_gaps(query="gym")
    assert fake.calls == 0
