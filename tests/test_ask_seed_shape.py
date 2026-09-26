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

    # #4194 / W7A — the seeder now MATCHES capture's turn write by embedding
    # every turn by DEFAULT, because a fixture without a vector BLINDS the
    # retrieval measurements to the dense leg (the instrument reported
    # ``retrieval_degraded`` 21/21 for that reason). The un-embedded
    # ``embed=False`` variant still models the pre-#4194 / no-embedder store
    # (#4197's backlog) and is pinned by
    # ``test_seed_capture_turn_store_embeds_by_default_and_can_model_the_backlog``.
    # Here we only pin the DEFAULT side; when no embedder is installed the
    # product cannot embed either, so the fail-soft shape is NULL per turn.
    from tortoise.embeddings import EmbeddingModel
    embedded = sdk._get_proj().g.query(
        "MATCH (t:Point) WHERE t.id STARTS WITH 'sess-x_t' "
        "AND t.embedding IS NOT NULL RETURN count(t)").result_set
    if EmbeddingModel.get() is None:
        assert embedded[0][0] == 0, embedded
    else:
        assert embedded[0][0] == len(ids), embedded

    # Pre-mutation blank gate: nothing at all is written for a blank session.
    assert seed_capture_turn_store(
        sdk, "sess-blank", [{"role": "user", "content": ""}]) == []
    after = sdk._get_proj().g.query(
        "MATCH (s:Session {id:'sess-blank'}) RETURN count(s)").result_set
    assert after[0][0] == 0


def test_seed_capture_turn_store_embeds_by_default_and_can_model_the_backlog(
        sdk):
    """W7A: the fixture stores the PRODUCT's own turn vector by default.

    The product's turn write now embeds every episodic turn (#4194), so a
    default fixture without a vector models a store the product no longer
    writes and BLINDS every retrieval measurement to the dense leg — the
    frozen instrument reported ``retrieval_degraded`` 21/21 for exactly that
    reason. ``embed=False`` seeds #4197's BACKLOG state (captured before the
    backfill, or with no embedder installed): no ``embedding`` at all. Both
    shapes are pinned here so a later edit cannot silently swap them.
    """
    from tortoise.embeddings import EmbeddingModel, compute_embedding
    from tortoise.search_engine import run_vector_query

    turns = [{"role": "user", "content": "the gym schedule is Monday"},
             {"role": "assistant", "content": "noted, Monday it is"}]
    proj = sdk._get_proj()
    has_embedder = EmbeddingModel.get() is not None

    # (a) DEFAULT — the product's own vector, on every turn of the session.
    ids = seed_capture_turn_store(sdk, "sess-emb", turns)
    assert ids == [f"sess-emb_t{i}" for i in range(len(turns))], ids
    embedded = proj.g.query(
        "MATCH (t:Point) WHERE t.id STARTS WITH 'sess-emb_t' "
        "AND t.embedding IS NOT NULL RETURN count(t)").result_set[0][0]
    if not has_embedder:
        # No embedder installed: the product cannot embed either, and the
        # fail-soft shape is NULL — never an invented stand-in vector.
        assert embedded == 0, embedded
    else:
        assert embedded == len(ids), (embedded, ids)

        # ... each stored vector IS the product encoder's own output for the
        # turn's stored text (same model, dimension, normalisation) — never a
        # stand-in. This also pins the batched composition equal to the single
        # ``compute_embedding`` the product's read path uses.
        import numpy as np
        rows = proj.g.query(
            "MATCH (t:Point) WHERE t.id STARTS WITH 'sess-emb_t' "
            "RETURN t.id, t.content, t.embedding ORDER BY t.id").result_set
        assert len(rows) == len(ids), rows
        for pid, stored_text, stored in rows:
            want = compute_embedding(stored_text)
            assert want is not None and len(want) == 384, (pid, want)
            assert len(stored) == len(want), (pid, len(stored))
            assert np.allclose(np.asarray(stored, dtype=float),
                               np.asarray(want, dtype=float), atol=1e-5), pid

        # ... and the read path's OWN vector leg is non-degraded on it.
        trace: list[dict] = []
        hits = run_vector_query(proj.g, compute_embedding("gym schedule"),
                                limit=10, leg_trace=trace)
        assert trace and trace[-1]["degraded"] is False, trace
        assert hits, "the seeded turn vector must be retrievable"
        assert all(h[0] in ids for h in hits), hits

    # (b) BACKLOG — the pre-#4194 / no-embedder shape: NO ``embedding``.
    # Asserted whether or not an embedder is installed, so the switch always
    # has coverage.
    seed_capture_turn_store(sdk, "sess-backlog", turns, embed=False)
    backlog = proj.g.query(
        "MATCH (t:Point) WHERE t.id STARTS WITH 'sess-backlog_t' "
        "AND t.embedding IS NOT NULL RETURN count(t)").result_set[0][0]
    assert backlog == 0, backlog


def test_seeder_turn_vectors_go_through_the_store_width_guard(
        sdk, monkeypatch):
    """W7A: the seeder routes through #4304's STORE seam, not the raw encoder.

    #4280: the vector-width constraint belongs to the store's Point HNSW index,
    so a write path must call ``encode_batch_for_store`` with
    ``proj.required_embedding_dim``. Calling the raw encoder instead has two
    failure shapes, and this test pins BOTH — only a path that consults
    ``required_embedding_dim`` can pass:

      * an INDEXED store declares :data:`EMBEDDING_DIM` — a wrong-width vector
        must degrade to NO vector, because storing it hands ``vecf32`` a
        vector the index cannot hold (a broken leg, not a near-miss);
      * the index-less brute-force lane declares ``None`` — a self-consistent
        vector of ANY width must be KEPT. Dropping it is the #4280 regression
        that emptied the cross-lens pool (``p.embedding IS NOT NULL``).

    The wrong width comes from a replaced ``EmbeddingModel``, so the test does
    not depend on the ambient lane or the real encoder's width.
    """
    import numpy as np

    from tortoise.embeddings import EMBEDDING_DIM, EmbeddingModel
    from tortoise.projection import FalkorProjection

    if EmbeddingModel.get() is None:
        pytest.skip("no embedder installed — the seeder fails soft to NULL")

    class _WrongDim:
        def encode(self, texts, batch_size=32, show_progress_bar=False):
            return np.zeros((len(texts), EMBEDDING_DIM - 1))

    turns = [{"role": "user", "content": f"width probe {i}"}
             for i in range(3)]

    def _stored(sid: str) -> list:
        return [r[0] for r in sdk._get_proj().g.query(
            "MATCH (t:Point) WHERE t.id STARTS WITH $p "
            "RETURN t.embedding ORDER BY t.id",
            params={"p": f"{sid}_t"}).result_set]

    try:
        monkeypatch.setattr(
            EmbeddingModel, "get",
            classmethod(lambda cls, load_timeout=None: _WrongDim()))
        EmbeddingModel._reset()

        # (a) INDEXED store: the wrong-width vector is DROPPED; the turn lands.
        monkeypatch.setattr(FalkorProjection, "required_embedding_dim",
                            property(lambda self: EMBEDDING_DIM))
        ids = seed_capture_turn_store(sdk, "sess-xguard", turns)
        assert len(ids) == len(turns), ids
        assert all(v is None for v in _stored("sess-xguard")), (
            _stored("sess-xguard"))

        # (b) NO index: the encoder's own width governs — the SAME vector is
        # KEPT (dropping it here is exactly the #4280 failure shape).
        monkeypatch.setattr(FalkorProjection, "required_embedding_dim",
                            property(lambda self: None))
        seed_capture_turn_store(sdk, "sess-noidx", turns)
        got = _stored("sess-noidx")
        assert got and all(v is not None for v in got), got
        assert all(len(v) == EMBEDDING_DIM - 1 for v in got), got
    finally:
        EmbeddingModel._reset()


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
    time. The shared seeder is told ``now=None`` for a seed with no
    ``session_date``, which since #4156 records NO time (session AND turns)
    rather than the run clock, so the reader's context carries no date marker
    and no value has to be erased afterwards."""
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


# ── 5. #4156: ``now=None`` records NO time, the default models a capture ──

def test_now_none_records_no_time_while_the_default_models_a_capture(sdk):
    """#4156: "no recorded time" and "the capture simulation's clock" must be
    two distinguishable values, not one.

    ``now`` used to default to ``None`` and be substituted with
    ``datetime.now()``, so a caller that wanted to record NO time got a
    fabricated date instead and had to erase it afterwards
    (``_clear_recorded_time``). The default is now the ``CAPTURE_CLOCK``
    sentinel — a capture always has a time — and an explicit ``now=None``
    means the session and its turns record NO time, REMOVING any time already
    on them.
    """
    from tools.ask_spotcheck import (
        CAPTURE_CLOCK,
        merge_capture_session,
    )

    proj = sdk._get_proj()

    # The default models a capture: it resolves the sentinel to the run clock
    # and WRITES it.
    resolved = merge_capture_session(sdk, "sess-clock", 2)
    assert resolved not in (None, CAPTURE_CLOCK), resolved
    assert proj.g.query(
        "MATCH (s:Session {id:'sess-clock'}) RETURN s.created_at",
    ).result_set == [[resolved]]

    # An explicit None records NO time — and no later call may resurrect one
    # (the write is a clear, never a coalesce of a fabricated default).
    assert merge_capture_session(sdk, "sess-notime", 2, now=None) is None
    assert merge_capture_session(sdk, "sess-notime", 2, now=None) is None
    assert proj.g.query(
        "MATCH (s:Session {id:'sess-notime'}) RETURN s.created_at",
    ).result_set == [[None]]

    # The turn side obeys the SAME contract.
    seed_capture_turn_store(
        sdk, "sess-turns-notime", [{"role": "user", "content": "undated"}],
        now=None)
    assert proj.g.query(
        "MATCH (t:Point {id:'sess-turns-notime_t0'}) "
        "RETURN t.createdAt, t.updatedAt",
    ).result_set == [[None, None]]

    # …and a session seeded with the default (no ``now``) still records its
    # capture time on both the session and its turns — a capture always has
    # one — so the fix cannot have turned the default into "no time".
    seed_capture_turn_store(
        sdk, "sess-turns-clock", [{"role": "user", "content": "dated"}])
    assert proj.g.query(
        "MATCH (s:Session {id:'sess-turns-clock'}) RETURN s.created_at",
    ).result_set[0][0]
    assert proj.g.query(
        "MATCH (t:Point {id:'sess-turns-clock_t0'}) RETURN t.createdAt",
    ).result_set[0][0]


def test_now_none_clears_a_time_already_on_the_node(sdk):
    """#4156: ``now=None`` must REMOVE a recorded time, not merely skip a write.

    ``_clear_recorded_time`` — the helper this change deletes — actively
    ``SET … = null`` on the session AND its turns. A "skip the write" reading
    would pass on a FRESH node and fail on a re-seed: the node keeps the
    previous call's (possibly fabricated, run-clock) date, which is the exact
    trap #4156 exists to remove. The contract is therefore enforced against
    the node, not against this call's write.
    """
    proj = sdk._get_proj()
    two_turns = [{"role": "user", "content": "dated one"},
                 {"role": "assistant", "content": "dated two"}]

    # First seeded WITH a time (the capture-clock default) and TWO turns.
    seed_capture_turn_store(sdk, "sess-reseed", two_turns)
    assert proj.g.query(
        "MATCH (s:Session {id:'sess-reseed'}) RETURN s.created_at",
    ).result_set[0][0]
    assert proj.g.query(
        "MATCH (:Session {id:'sess-reseed'})-[:CONTAINS]->(t:Point) "
        "RETURN count(t.createdAt)",
    ).result_set == [[2]]

    # Re-seeded as an UNDATED session, with a SHORTER conversation — the
    # recorded time must be GONE from the session and from EVERY stored turn,
    # including the one this call does not rewrite.
    seed_capture_turn_store(
        sdk, "sess-reseed", two_turns[:1], now=None)
    assert proj.g.query(
        "MATCH (s:Session {id:'sess-reseed'}) RETURN s.created_at",
    ).result_set == [[None]]
    assert proj.g.query(
        "MATCH (:Session {id:'sess-reseed'})-[:CONTAINS]->(t:Point) "
        "RETURN t.id, t.createdAt, t.updatedAt ORDER BY t.id",
    ).result_set == [
        ["sess-reseed_t0", None, None],
        ["sess-reseed_t1", None, None],
    ]


def test_non_none_now_is_recorded_verbatim(sdk):
    """#4156: only ``None`` means "no recorded time" — the fix must not swap
    one silent coercion for another.

    The pre-fix ``now = now or datetime.now()`` turned a FALSY string into the
    run clock; under the new contract the sentinel is the only "use the
    capture clock" spelling, so any ``str`` is recorded as given (the read
    path renders a non-date as UNKNOWN, `_iso_date10`).
    """
    from tools.ask_spotcheck import merge_capture_session

    assert merge_capture_session(sdk, "sess-empty", 1, now="") == ""
    assert sdk._get_proj().g.query(
        "MATCH (s:Session {id:'sess-empty'}) RETURN s.created_at",
    ).result_set == [[""]]
    assert merge_capture_session(
        sdk, "sess-word", 1, now="not-a-date") == "not-a-date"
    assert sdk._get_proj().g.query(
        "MATCH (s:Session {id:'sess-word'}) RETURN s.created_at",
    ).result_set == [["not-a-date"]]
