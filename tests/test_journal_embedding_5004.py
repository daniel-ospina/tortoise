"""#5004 — the journal carries the embedding, and a replay restores it VERBATIM.

THE DEFECT THIS PINS
--------------------
`docs/durability-posture.md` §*Derived properties that are STORED* records
R1; the design source is `docs/architecture/STORAGE-ARCHITECTURE.md` §3/§14.1
O1 (landed via #5016). The invariant the storage design rests on::

    derived tables  =  replay(the journal)      NOT  recompute(the sources)

It was FALSE for the embedding — the design's largest cost lever. ``_emit_event``
stripped ``embedding`` from the JSONL payload ("it is recomputed on replay"),
and ``_upsert_point_props`` re-encoded it from ``content``. So a replay with a
DIFFERENT (or revised, or provider-changed) embedder silently produced a
different graph from the same journal.

RULING HONOURED (R1 — `docs/durability-posture.md`)
--------------------------------------------------------------
**The embedding STORES; it is not regenerated.** A re-embed is a RE-RUN, not a
replay. The journal now carries the vector plus the identity it was computed
under (model id, pinned revision, text hash). A replay restores it verbatim;
a model change is a RECORDED decision point, never a silent re-encode.

SCOPE — an EXPLICIT exemption, field by field (not class by class)
------------------------------------------------------------------
The **`PointRevised`** path is deliberately NOT covered here. Measured on the
base tree: live ``update_point(content=...)`` does not change the node's vector
at all, while the replay's ``_revise_point`` re-encodes — the divergence runs
the OTHER way and its root is the LIVE writer, not the journal. That is the
content-edit-staleness class (#4206 / #4208 / #4302), which #5004 itself lists
as *related, not duplicate*. This module does not claim coverage of it; the
last test below PINS the exemption so it cannot be mistaken for a gap nobody
noticed.

Run (docker lane):
  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
      uv run pytest tests/test_journal_embedding_5004.py -q --tb=short
"""
from __future__ import annotations

import json
import logging
from unittest import mock

import pytest

from tortoise.embeddings import (
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    EMBEDDING_MODEL_REVISION,
)
from tortoise.ids import content_hash as _text_hash
from tortoise.sdk import TortoiseSDK

_EMBED_PATCH = "tortoise.embeddings.compute_embedding"
_DIM = EMBEDDING_DIM if isinstance(EMBEDDING_DIM, int) else 384


def _embed_a(text: str, max_tokens: int = 512):
    """Deterministic vector from embedder A."""
    return [float(len(text))] + [0.25] * (_DIM - 1)


def _embed_b(text: str, max_tokens: int = 512):
    """A DIFFERENT deterministic vector — same length, different components.

    Stands in for the changed model / changed revision / provider-side change
    the issue names: ``embed("x")`` is not pure with respect to the journal.
    """
    return [float(len(text)) + 100.0] + [0.75] * (_DIM - 1)


def _batch(embedder):
    """A deterministic stand-in for the TURN batch seam.

    `_capture_turn_embeddings` goes through `encode_batch_for_store` (the plural
    `compute_embeddings`), NOT `compute_embedding` — so patching only the latter
    left the live turn vector coming from the REAL sentence-transformers model.
    The test then depended on that model loading (offline / not-installed CI
    would fail) and could not assert a specific vector. Patching both seams
    makes the turn tests hermetic and exactly discriminating.
    """
    def _b(texts, expected_dim=None):
        return [embedder(t) for t in texts]
    return _b


_BATCH_PATCH = "tortoise.embeddings.encode_batch_for_store"
_BATCH_A = _batch(_embed_a)
_BATCH_B = _batch(_embed_b)


@pytest.fixture
def sup(tmp_path):
    """(events_dir, sdk) with the JSONL journal wired."""
    events = tmp_path / "events"
    events.mkdir()
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "graph.db"),
                          event_log_path=str(events / "events.jsonl"))
        yield events, sdk
    sdk.close()


def _events(events_dir) -> list[dict]:
    out = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _vector(sdk: TortoiseSDK, pid: str):
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.embedding", params={"id": pid}
    ).result_set
    assert rows, f"point {pid} missing from the graph"
    return None if rows[0][0] is None else list(rows[0][0])


# ── 1. the artifact assertion ────────────────────────────────────────────

def test_journal_carries_the_embedding_and_its_identity(sup):
    """The journal must carry the vector + model/revision/text-hash.

    FAILS BEFORE: 0/3 events carry `embedding` (the strip at sdk.py).
    """
    events, sdk = sup
    made = {}
    for i in range(3):
        content = f"journaled embedding instance {i}"
        p = sdk.create_point("statement", content)
        made[p["id"]] = content

    adds = [e for e in _events(events) if e.get("type") == "PointAdded"]
    assert len(adds) == 3, f"expected 3 PointAdded events, got {len(adds)}"

    for ev in adds:
        pt = ev["point"]
        pid = pt["id"]
        assert "embedding" in pt, "journal did not carry the embedding (#5004)"
        assert isinstance(pt["embedding"], list)
        assert len(pt["embedding"]) == _DIM
        assert all(isinstance(x, float) for x in pt["embedding"])
        assert pt["embedding_model"] == EMBEDDING_MODEL
        assert pt["embedding_revision"] == EMBEDDING_MODEL_REVISION
        assert pt["embedding_text_hash"] == _text_hash(made[pid])
        # content_hash is a PURE function of content — it stays recomputable
        # and therefore stays OUT of the record (#2795 D2).
        assert "content_hash" not in pt, "content_hash must stay recomputed"


# ── 2. THE FALSIFIER for the issue's thesis ──────────────────────────────

def test_replay_restores_the_journalled_vector_verbatim_under_a_changed_embedder(sup):
    """Same journal + a changed embedder ⇒ the SAME graph.

    This is the issue's central claim, inverted: before the fix, replaying the
    journal under embedder B produced B's vectors — a *different graph from the
    same journal*, silently. After the fix the journalled vector (A's) is
    restored verbatim.

    FAILS BEFORE: the rebuilt vector equals B's output.
    """
    events, sdk = sup
    pid = sdk.create_point("statement", "verbatim restore probe").get("id")
    live_a = _vector(sdk, pid)
    assert live_a == _embed_a("verbatim restore probe")

    with mock.patch(_EMBED_PATCH, _embed_b):
        sdk._get_proj().rebuild_all(str(events))

    rebuilt = _vector(sdk, pid)
    assert rebuilt == live_a, (
        "replay re-encoded the embedding instead of restoring the journalled "
        "vector — a changed embedder silently changed the graph (#5004)"
    )
    assert rebuilt != _embed_b("verbatim restore probe")


# ── 3. the decision point is RECORDED, not silent ────────────────────────

def test_replay_records_the_embedder_identity_mismatch(sup, caplog, monkeypatch):
    """A model change must be visible, not silent.

    Both halves differ here (the vector function AND the declared identity),
    which is the full "the embedder changed" shape.
    """
    events, sdk = sup
    pid = sdk.create_point("statement", "identity mismatch probe").get("id")
    live_a = _vector(sdk, pid)

    monkeypatch.setattr("tortoise.embeddings.EMBEDDING_MODEL", "other/model-v9")
    monkeypatch.setattr("tortoise.embeddings.EMBEDDING_MODEL_REVISION", "deadbeef")
    with caplog.at_level(logging.WARNING), mock.patch(_EMBED_PATCH, _embed_b):
        sdk._get_proj().rebuild_all(str(events))

    assert _vector(sdk, pid) == live_a, "still must restore the journal's vector"
    text = caplog.text
    assert "restoring the JOURNALED vector verbatim" in text
    assert EMBEDDING_MODEL in text and "other/model-v9" in text


# ── 4. a strip-era journal still replays (back-compat) ───────────────────

def test_legacy_journal_without_a_vector_still_recomputes(tmp_path):
    """A pre-#5004 record carries no vector — there it must still recompute.

    Otherwise every existing journal would come back with NULL embeddings.
    """
    events = tmp_path / "events"
    events.mkdir()
    pid = "1a0d0000000-legacy00000001"
    content = "pre-5004 strip-era record"
    (events / "events.jsonl").write_text(json.dumps({
        "event_id": "e-legacy-1", "ts": "2026-09-01T00:00:00+00:00",
        "type": "PointAdded", "initiated_by": "sdk", "projection_version": 2,
        "point": {"id": pid, "content": content, "pointKind": "statement"},
    }) + "\n")

    with mock.patch(_EMBED_PATCH, _embed_b):
        sdk = TortoiseSDK(str(tmp_path / "legacy.db"))
        try:
            sdk._get_proj().rebuild_all(str(events))
            assert _vector(sdk, pid) == _embed_b(content), (
                "a legacy record with no journalled vector must fall back to "
                "recomputation"
            )
        finally:
            sdk.close()


# ── 5. the #4305 boundary must not regress ───────────────────────────────

def test_graph_only_point_vector_still_comes_from_the_prewipe_capture(tmp_path):
    """A point with NO journal record is NOT covered by #5004.

    Its vector can only come from the live pre-wipe capture. #5004 narrowed the
    journal path; it must not have touched this one.
    """
    events = tmp_path / "events"
    events.mkdir()
    pid = "1a0d0000000-graphonly0000001"
    (events / "events.jsonl").write_text("")

    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "g.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            sdk._get_proj()._upsert(
                {"id": pid, "content": "graph only seed", "pointKind": "statement"})
            before = _vector(sdk, pid)
            assert before == _embed_a("graph only seed")
            sdk._get_proj().rebuild_all(str(events))
            assert _vector(sdk, pid) == before, (
                "the graph-only pre-wipe-capture restoration regressed (#4305)"
            )
        finally:
            sdk.close()


# ── 6. review round-1 findings — regression pins ─────────────────────────

def test_a_nonfinite_or_oversized_journal_vector_degrades_and_never_raises(tmp_path):
    """A corrupt journal line must NOT strand the rebuild after the wipe.

    `float('nan')`/`float('inf')` do not raise, and `json` round-trips them, so
    an earlier version of the guard let a `NaN` vector reach `vecf32()` and
    raise AFTER the wipe on every retry. A huge JSON int raises
    `OverflowError`, which is not a `TypeError`. Both must DEGRADE — and after
    round 3 the degradation is MORE precise than "recompute": the key stays
    present as an owned `None`, so the replay leaves the node vectorless
    rather than inventing a vector from a record that FAILED to carry one.
    Never raise.
    """
    events = tmp_path / "events"
    events.mkdir()
    nan_line = json.dumps({
        "event_id": "e-nan", "ts": "2026-09-01T00:00:00+00:00",
        "type": "PointAdded", "initiated_by": "sdk", "projection_version": 2,
        "point": {"id": "1a0d0000000-nanvec00000001", "content": "nan probe",
                  "pointKind": "statement", "embedding": [float("nan")] * _DIM,
                  "embedding_model": EMBEDDING_MODEL},
    })
    big_line = json.dumps({
        "event_id": "e-big", "ts": "2026-09-01T00:00:01+00:00",
        "type": "PointAdded", "initiated_by": "sdk", "projection_version": 2,
        "point": {"id": "1a0d0000000-bigint0000001", "content": "huge probe",
                  "pointKind": "statement", "embedding": [10 ** 400],
                  "embedding_model": EMBEDDING_MODEL},
    })
    assert "NaN" in nan_line, "json must be able to emit the corrupt shape"
    (events / "events.jsonl").write_text(nan_line + "\n" + big_line + "\n")

    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "nan.db"))
        try:
            # MUST NOT raise — the rebuild wipes first, so an exception here
            # leaves the graph destroyed and every retry equally broken.
            sdk._get_proj().rebuild_all(str(events))
            for pid, _content in (("1a0d0000000-nanvec00000001", "nan probe"),
                                  ("1a0d0000000-bigint0000001", "huge probe")):
                vec = _vector(sdk, pid)
                assert vec is None, (
                    "an unusable journal vector must degrade to NO vector — the "
                    "key is owned as None, so the replay must not recompute "
                    "one the journal never recorded (#5004 round-3)")
        finally:
            sdk.close()


def test_identity_keys_are_never_node_properties(sup):
    """The identity is PAYLOAD metadata — it must not become a node prop.

    The live writer stamps it only on the journal copy; persisting it on replay
    would make replay diverge from live by three properties (the #330/#3312
    parity class). This is the assertion the `_POINT_HANDLED` entry exists for.
    """
    events, sdk = sup
    pid = sdk.create_point("statement", "identity prop probe").get("id")
    sdk._get_proj().rebuild_all(str(events))
    props = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)", params={"id": pid},
    ).result_set[0][0]
    for key in ("embedding_model", "embedding_revision", "embedding_text_hash"):
        assert key not in props, f"{key} leaked onto the node as a property"


def test_a_wrong_width_journal_vector_is_refused_and_recorded(tmp_path, caplog):
    """An unwritable-width vector is REFUSED, not silently re-encoded.

    Writing it is impossible; recomputing it would store a vector whose model
    the journal never recorded. The refusal is the R1-respecting choice and it
    must be visible (once per width/dim pair, not once per point).
    """
    events = tmp_path / "events"
    events.mkdir()
    pid = "1a0d0000000-width00000001"
    (events / "events.jsonl").write_text(json.dumps({
        "event_id": "e-w", "ts": "2026-09-01T00:00:00+00:00",
        "type": "PointAdded", "initiated_by": "sdk", "projection_version": 2,
        "point": {"id": pid, "content": "width probe",
                  "pointKind": "statement", "embedding": [0.1] * 10,
                  "embedding_model": EMBEDDING_MODEL},
    }) + "\n")

    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "w.db"))
        proj = sdk._get_proj()
        try:
            with mock.patch.object(type(proj), "required_embedding_dim",
                                   new_callable=mock.PropertyMock,
                                   return_value=_DIM), \
                    caplog.at_level(logging.WARNING):
                proj.rebuild_all(str(events))
            assert _vector(sdk, pid) is None, (
                "a wrong-width journal vector must not be written")
            assert "leaving it unset" in caplog.text
        finally:
            sdk.close()


def test_eventapi_producer_journals_the_vector(tmp_path):
    """The SECOND producer (ingest/mining/CLI) must journal the vector too.

    Otherwise `derived = replay(journal)` stays false for every Point an
    ingest/mine lane creates — the same defect, on a different producer.
    """
    from tortoise.api import EventAPI, provenance
    from tortoise.log import EventLog

    events = tmp_path / "events"
    events.mkdir()
    log = EventLog(events / "events.jsonl")
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "api.db"))
        try:
            api = EventAPI(log, initiated_by="extractor", projection=sdk._get_proj())
            api.add_point("eventapi producer probe", provenance("src:1", [0, 4], "q"))
            recorded = [e for e in _events(events) if e.get("type") == "PointAdded"]
            assert recorded, "EventAPI did not journal a PointAdded"
            pt = recorded[0]["point"]
            assert "embedding" in pt, "EventAPI journaled no vector (#5004)"
            assert len(pt["embedding"]) == _DIM
            assert pt["embedding_model"] == EMBEDDING_MODEL
            assert pt["embedding_revision"] == EMBEDDING_MODEL_REVISION
        finally:
            sdk.close()


def test_a_reemit_does_not_attest_a_text_hash():
    """Only a CREATING event may claim "computed from this content".

    A re-emitted snapshot can carry a vector that is stale w.r.t. the content
    it rides with (the content-edit path does not re-embed), so stamping a
    text-hash there would launder a stale vector as fresh.
    """
    from tortoise.embeddings import stamp_journal_embedding

    creating = stamp_journal_embedding(
        {"id": "p1", "content": "c", "embedding": [0.5] * _DIM}, creating=True)
    assert "embedding_text_hash" in creating

    reemit = stamp_journal_embedding(
        {"id": "p2", "content": "c", "embedding": [0.5] * _DIM,
         "embedding_text_hash": "stale", "embedding_model": "stale-model"},
        creating=False)
    assert "embedding_text_hash" not in reemit, (
        "a re-emit must not attest the vector to the content it rides with")
    # Round-2 P4: and it must not mis-attest the ORIGIN either — the snapshot's
    # vector may predate a model change.
    assert "embedding_model" not in reemit
    assert "embedding_revision" not in reemit
    assert reemit["embedding"] == [0.5] * _DIM, "the vector itself is kept"


# ── 7. round-2 review findings — regression pins ─────────────────────────

def test_a_refused_width_is_not_resurrected_by_the_prewipe_snapshot(tmp_path):
    """`rebuild_all` must not resurrect a vector the journal refused to write.

    Round-2 P2: marking `journal_embed_write` only on `wrote_embedding` meant a
    REFUSED (wrong-width) journal vector left the id unmarked, so the pass-1b
    tail re-applied the OLDER pre-wipe snapshot. `rebuild_all` was then a
    function of pre-wipe graph state — a populated store, `rebuild()`, and a
    fresh store disagreed — which is precisely the invariant this issue is
    about.
    """
    events = tmp_path / "events"
    events.mkdir()

    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "pop.db"),
                          event_log_path=str(events / "events.jsonl"))
        proj = sdk._get_proj()
        try:
            pid = sdk.create_point("statement", "resurrect probe").get("id")

            # Rewrite the journaled vector to a width this store cannot hold.
            log = events / "events.jsonl"
            lines = []
            for line in log.read_text().splitlines():
                ev = json.loads(line)
                if (ev.get("type") == "PointAdded"
                        and (ev.get("point") or {}).get("id") == pid):
                    ev["point"]["embedding"] = [0.1] * 10
                lines.append(json.dumps(ev))
            log.write_text("\n".join(lines) + "\n")

            with mock.patch.object(type(proj), "required_embedding_dim",
                                   new_callable=mock.PropertyMock,
                                   return_value=_DIM):
                proj.rebuild_all(str(events))
            assert _vector(sdk, pid) is None, (
                "the pre-wipe snapshot resurrected a refused vector")
        finally:
            sdk.close()

    # A FRESH store replaying the same journal must agree (no pre-wipe state).
    with mock.patch(_EMBED_PATCH, _embed_a):
        fresh = TortoiseSDK(str(tmp_path / "fresh.db"))
        fresh_proj = fresh._get_proj()
        try:
            with mock.patch.object(type(fresh_proj), "required_embedding_dim",
                                   new_callable=mock.PropertyMock,
                                   return_value=_DIM):
                fresh_proj.rebuild_all(str(events))
            assert _vector(fresh, pid) is None, (
                "populated-store and fresh-store rebuilds disagree")
        finally:
            fresh.close()


def test_session_turn_producer_journals_the_vector(tmp_path, monkeypatch):
    """The episodic turn producer must journal the vector it stored live.

    Round-2 P2: the highest-volume Point producer wrote `emb` to the node but
    the `PointAdded` snapshot carried no vector, so replay re-encoded and a
    changed embedder silently changed every captured turn's vector.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    events = tmp_path / "events"
    events.mkdir()
    conv = [
        {"role": "user", "content": "the auth dead-end is the top issue"},
        {"role": "assistant", "content": "agreed, ship serve --http first"},
    ]
    with mock.patch(_EMBED_PATCH, _embed_a), mock.patch(_BATCH_PATCH, _BATCH_A):
        sdk = TortoiseSDK(str(tmp_path / "c.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            sdk.capture_session(conv)
            turn_events = [
                e for e in _events(events)
                if e.get("type") == "PointAdded"
                and (e.get("point") or {}).get("pointKind") == "event"
            ]
            assert turn_events, "capture_session journaled no turn Points"
            for e in turn_events:
                assert "embedding" in e["point"], (
                    "a captured turn's vector was not journaled (#5004)")
                assert e["point"]["embedding_model"] == EMBEDDING_MODEL

            pid = turn_events[0]["point"]["id"]
            before = _vector(sdk, pid)
            # Exact value now that the batch seam is deterministic (the live
            # turn vector used to come from the real model).
            assert before == _embed_a(turn_events[0]["point"]["content"]), (
                "the live turn vector is not the batch embedder's output")

            with mock.patch(_EMBED_PATCH, _embed_b):
                sdk._get_proj().rebuild_all(str(events))
            assert _vector(sdk, pid) == before, (
                "a captured turn's vector changed on replay")
        finally:
            sdk.close()


def test_identity_keys_cannot_be_set_through_props(tmp_path):
    """The journal identity keys are server-managed — reject them fail-closed.

    Round-2 P3: the replay drops them (`_POINT_HANDLED`), so a caller-set value
    would persist live and vanish on rebuild — a live/replay parity break.
    """
    sdk = TortoiseSDK(str(tmp_path / "props.db"))
    try:
        for key in ("embedding_model", "embedding_revision",
                    "embedding_text_hash"):
            with pytest.raises(ValueError, match="server-managed"):
                sdk.create_point("statement", "x", **{key: "tenant-supplied"})
    finally:
        sdk.close()


# ── 8. the declared exemption, pinned so it cannot be mistaken for coverage ─

def test_revise_path_is_explicitly_out_of_scope_for_5004(sup):
    """#5004 does NOT cover `PointRevised` — pin BOTH shapes, don't hide it.

    TWO shapes live on this path and #5004 covers NEITHER:

    1. **Content edit** (measured here): live ``update_point(content=...)``
       leaves the vector UNCHANGED, while the replay's ``_revise_point``
       re-encodes — so the divergence runs the other way and its root is the
       LIVE writer, not the journal (#4206/#4208/#4302). This is the shape
       #5004's own text lists as *related, not duplicate*.
    2. **Caller-supplied vector** (``update_point(id, embedding=[...])``): live
       writes the caller's vector AND the record carries it, but the replay
       ignores it and re-encodes. Here the journal is SUFFICIENT and is simply
       not read — shape 1's rationale ("the root is the live writer") does not
       reach it. Verified present on `origin/main`; filed as **#5046**.

    This test records the state #5004 leaves behind so a later lane sees it was
    deliberate. When those issues land, this pin must be INVERTED, not deleted.
    """
    events, sdk = sup
    pid = sdk.create_point("statement", "original").get("id")
    v_created = _vector(sdk, pid)

    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk.update_point(pid, content="a substantially longer replacement")
    assert _vector(sdk, pid) == v_created, (
        "exemption premise changed: live update_point now recomputes the "
        "vector — revisit whether #5004 must extend to PointRevised"
    )

    # And no vector is journalled on the content-edit revise record itself.
    revises = [e for e in _events(events) if e.get("type") == "PointRevised"]
    assert revises, "expected a PointRevised record"
    assert all("embedding" not in (e.get("point") or {})
               and "embedding" not in e for e in revises)

    # Shape 2 — the journal carries the vector and the replay ignores it
    # (#5046). Pinned so it is a DECLARED gap, not an unnoticed one.
    v_before = _vector(sdk, pid)
    caller_vec = [0.2] * _DIM
    sdk.update_point(pid, embedding=caller_vec)
    assert _vector(sdk, pid) == caller_vec, (
        "shape-2 premise changed: the live revise no longer takes the caller "
        "vector — #5046 may be fixed; INVERT this pin"
    )
    carrier = [e for e in _events(events) if e.get("type") == "PointRevised"]
    assert any("embedding" in e for e in carrier), (
        "shape-2 premise changed: the revise record no longer carries the "
        "vector — #5046 may be fixed; INVERT this pin"
    )
    with mock.patch(_EMBED_PATCH, _embed_b):
        sdk._get_proj().rebuild_all(str(events))
    assert _vector(sdk, pid) != caller_vec, (
        "#5046 appears FIXED on this tree — the PointRevised replay now "
        "restores the journalled vector. Invert this pin and drop it from "
        "the exemption (kept as a guard so the fix cannot land unnoticed)"
    )
    assert v_before is not None


# ── 9. round-3 review findings — regression pins ─────────────────────────
#
# Round 3 falsified the FIRST version of the turn-producer fix with a shape the
# round-2 test never exercised: a re-capture made while the embedder is
# UNAVAILABLE. The live turn write PRESERVES the node's vector in that case
# (same content, `prior_ch = turn.ch`), but the snapshot only carried
# `turn_embs[i]` — so the journal's second `PointAdded` for the id carried NO
# vector, the replay read that as "legacy record, recompute", and the vector
# the FIRST capture had journalled was overwritten. The fix is the
# PRESENCE-IS-OWNERSHIP rule: a producer that owns the field always writes the
# key (the vector, or an explicit `None`), and the replay restores-or-leaves-
# unset, never invents.

def test_turn_recapture_with_the_embedder_down_keeps_the_journalled_vector(
        tmp_path, monkeypatch):
    """A vector-less RE-CAPTURE must not overwrite the journalled vector.

    Reproduction (round-3 P2): capture 1 under embedder A; capture 2 with the
    SAME content and the embedder DOWN. The live write preserves A on the node,
    but the journal gained a second `PointAdded` with no vector — and a rebuild
    under embedder B then set the turn to B while live still held A.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    events = tmp_path / "events"
    events.mkdir()
    conv = [
        {"role": "user", "content": "keep the turn vector stable across recapture"},
        {"role": "assistant", "content": "the live write preserves it"},
    ]

    def _embedder_down(*_a, **_k):
        raise RuntimeError("embedder unavailable (simulated)")

    # An EXPLICIT session id on both captures: the turn ids are
    # `{session_id}_t{i}`, and the whole point here is the SECOND write to the
    # SAME id (a fresh auto-minted session would write different nodes).
    sid = "resp_recapture_probe"
    with mock.patch(_EMBED_PATCH, _embed_a), mock.patch(_BATCH_PATCH, _BATCH_A):
        sdk = TortoiseSDK(str(tmp_path / "recap.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            sdk.capture_session(conv, session_id=sid)
            first = [e["point"] for e in _events(events)
                     if e.get("type") == "PointAdded"
                     and (e.get("point") or {}).get("pointKind") == "event"]
            assert first, "capture 1 journaled no turns"
            pid = first[0]["id"]
            live_before = _vector(sdk, pid)
            assert live_before is not None, (
                "premise: capture 1 stored a vector for the turn")
            assert first[0].get("embedding") is not None, (
                "premise: capture 1 journalled the vector it stored")

            # Capture 2 — identical content, embedder DOWN.
            with mock.patch("tortoise.embeddings.encode_batch_for_store",
                            _embedder_down):
                sdk.capture_session(conv, session_id=sid)

            # The LIVE write preserved the prior vector (this is the trap).
            assert _vector(sdk, pid) == live_before, (
                "premise changed: the live re-capture no longer preserves the "
                "vector for unchanged content — revisit this pin")

            # ... and the LAST journal record for that id carries it too.
            snapshots = [e["point"] for e in _events(events)
                         if e.get("type") == "PointAdded"
                         and (e.get("point") or {}).get("id") == pid]
            assert len(snapshots) >= 2, "expected a second PointAdded"
            assert "embedding" in snapshots[-1], (
                "the re-capture journalled NO vector for an id the write "
                "PRESERVED — the replay would recompute over it (round-3 P2)")
            assert snapshots[-1]["embedding"] is not None

            with mock.patch(_EMBED_PATCH, _embed_b):
                sdk._get_proj().rebuild_all(str(events))
            assert _vector(sdk, pid) == live_before, (
                "replay overwrote a PRESERVED vector with the active "
                "embedder's output (round-3 P2)")
        finally:
            sdk.close()


def test_point_created_with_the_embedder_down_gains_no_vector_on_replay(tmp_path):
    """An owned `None` means \"no vector\" — the replay must not invent one.

    The producer sets the `embedding` key even when it has no vector, so the
    journal can distinguish "the writer says there is none" from "a strip-era
    record that never carried the field" (which recomputes). Without that, a
    point created during an embedder outage silently acquired a vector on the
    next rebuild — a graph the live store never had.
    """
    events = tmp_path / "events"
    events.mkdir()

    def _embedder_down(_text, max_tokens=512):
        raise RuntimeError("embedder unavailable (simulated)")

    with mock.patch(_EMBED_PATCH, _embedder_down):
        sdk = TortoiseSDK(str(tmp_path / "novec.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            pid = sdk.create_point("statement", "created with no embedder").get("id")
            assert _vector(sdk, pid) is None, "premise: live node has no vector"

            rec = [e["point"] for e in _events(events)
                   if e.get("type") == "PointAdded"
                   and (e.get("point") or {}).get("id") == pid]
            assert rec and "embedding" in rec[0], (
                "the producer must OWN the field (present, None) — an absent "
                "key is indistinguishable from a legacy record")
            assert rec[0]["embedding"] is None
            assert "embedding_model" not in rec[0], (
                "no vector means nothing to attest")

            # A replay under a WORKING embedder must still leave it unset.
            with mock.patch(_EMBED_PATCH, _embed_a):
                sdk._get_proj().rebuild_all(str(events))
            assert _vector(sdk, pid) is None, (
                "replay invented a vector the live node never had (round-3)")
        finally:
            sdk.close()


def test_a_caller_supplied_vector_is_restored_verbatim(tmp_path):
    """A caller-owned vector keeps its RAW form across a rebuild.

    `create_point` stores a caller-supplied `embedding` verbatim (`$embedding`,
    never `vecf32`) — a recorded decision (PR #3018 review P2). The journal now
    marks such a payload `embedding_verbatim`, so the replay writes it raw too
    instead of narrowing it to float32 (`0.1` -> `0.10000000149011612`) and
    disagreeing with live. And because the vector's origin is the CALLER's, the
    journal must not stamp a server identity onto it.
    """
    events = tmp_path / "events"
    events.mkdir()
    caller_vec = [0.1] * _DIM
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "verbatim.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            pid = sdk.create_point("statement", "caller-owned vector",
                                   embedding=caller_vec).get("id")
            assert _vector(sdk, pid) == caller_vec

            rec = [e["point"] for e in _events(events)
                   if e.get("type") == "PointAdded"
                   and (e.get("point") or {}).get("id") == pid]
            assert rec, "no PointAdded journalled"
            assert rec[0].get("embedding_verbatim") is True, (
                "the create site must tell the journal the vector is "
                "caller-owned, so the replay stores it raw")
            assert "embedding_model" not in rec[0], (
                "a caller-owned vector must not be attested as server-computed")

            with mock.patch(_EMBED_PATCH, _embed_b):
                sdk._get_proj().rebuild_all(str(events))
            assert _vector(sdk, pid) == caller_vec, (
                "the caller-supplied vector was narrowed to float32 (or "
                "recomputed) on replay")
            props = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            # #5004 round-3: the marker is a DECLARED NODE PROPERTY, not
            # payload-only metadata — every later re-emit reads the point back
            # through `get_point`, so a payload-only flag was lost on promote
            # and the replay narrowed the vector. Live and replay must AGREE
            # on its presence (absent on a server-vectored point, true here).
            assert props.get("embedding_verbatim") is True, (
                "the verbatim marker must persist on the node so a later "
                "promote/merge re-emit still carries it")
        finally:
            sdk.close()


# ── 10. round-4 review findings — regression pins ────────────────────────
#
# Round 3 made the verbatim marker JOURNAL-PAYLOAD metadata, which is lost the
# moment a point is re-emitted from a graph read: `promote_point` (and every
# other re-emit) passes `point=self.get_point(pid)`, which cannot recover the
# caller-vs-server distinction. The replay then took the `vecf32` branch and
# narrowed a caller-owned float64 vector. The marker is now a DECLARED NODE
# PROPERTY, so it rides `get_point` for free. Two more round-4 findings are
# pinned below: the marker must be server-managed (a tenant must not be able to
# forge it — it both flips the storage form and silences the R1 attestation),
# and `_record_embedding_identity` must not raise on content it cannot hash.

def test_a_promoted_caller_vectored_point_keeps_its_raw_vector(tmp_path):
    """`promote_point` must not narrow a caller-owned vector on replay.

    Round-4 P1 (found independently by two reviewers): the verbatim marker was
    payload-only, so the `PointPromoted` snapshot — read back via `get_point` —
    carried the vector with no marker, and the replay narrowed it through
    `vecf32` (`0.1` -> `0.10000000149011612`) while live held the raw float64.
    """
    events = tmp_path / "events"
    events.mkdir()
    caller_vec = [0.1] * _DIM
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "promote.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            pid = sdk.create_point("statement", "promote a caller vector",
                                   embedding=caller_vec).get("id")
            assert _vector(sdk, pid) == caller_vec
            sdk.promote_point(pid)
            live_after = _vector(sdk, pid)
            assert live_after == caller_vec, (
                "premise: the live promote leaves the caller vector alone")

            with mock.patch(_EMBED_PATCH, _embed_b):
                sdk._get_proj().rebuild_all(str(events))
            assert _vector(sdk, pid) == live_after, (
                "a promoted caller-supplied vector was narrowed to float32 on "
                "replay — the verbatim marker did not survive the promote "
                "(round-4 P1)")
        finally:
            sdk.close()


def test_the_verbatim_marker_cannot_be_forged_through_props(tmp_path):
    """The marker is server-owned: forging it would flip storage AND silence R1.

    Round-4 P2: a tenant-supplied `embedding_verbatim=True` persisted live,
    vanished on replay (a parity break), and made `stamp_journal_embedding` skip
    the model/text-hash attestation — silencing the model-change record R1
    exists to keep.
    """
    sdk = TortoiseSDK(str(tmp_path / "forge.db"))
    try:
        for key in ("embedding_verbatim", "embedding_preserved",
                    "embedding_model", "embedding_revision",
                    "embedding_text_hash"):
            with pytest.raises(ValueError, match="server-managed"):
                sdk.create_point("statement", "x", **{key: "tenant-supplied"})
    finally:
        sdk.close()


def test_content_that_cannot_be_hashed_does_not_raise_on_replay():
    """A lone surrogate must not raise AFTER the wipe.

    Round-4 P1: `_record_embedding_identity` called `_content_hash(content)`
    unguarded, and `_content_hash` is `text.encode("utf-8")` — which raises
    `UnicodeEncodeError` on a lone surrogate. This runs inside `rebuild_all`,
    after the wipe and before the graph write, so one foreign/hand-edited line
    would destroy the graph and strand every retry. A hash that cannot be
    computed simply cannot be compared.
    """
    from tortoise.projection.entities import _record_embedding_identity

    _record_embedding_identity(          # must not raise
        {"id": "p", "content": "\ud800", "embedding_model": "m",
         "embedding_revision": "r", "embedding_text_hash": "whatever"},
        set())


def test_a_preserved_turn_vector_is_not_attested_to_the_active_model(
        tmp_path, monkeypatch):
    """A captured-and-PRESERVED vector must not claim the current model.

    Round-4 P2: the second capture encodes nothing, so the record carries the
    vector the write PRESERVED — computed by whatever model ran the FIRST
    capture. Stamping the active model on it records a false origin, and hides
    a model change entirely whenever the original record is gone.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    events = tmp_path / "events"
    events.mkdir()
    conv = [{"role": "user", "content": "attestation of a preserved vector"}]

    def _embedder_down(*_a, **_k):
        raise RuntimeError("embedder unavailable (simulated)")

    sid = "resp_attest_probe"
    with mock.patch(_EMBED_PATCH, _embed_a), mock.patch(_BATCH_PATCH, _BATCH_A):
        sdk = TortoiseSDK(str(tmp_path / "attest.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            sdk.capture_session(conv, session_id=sid)
            pid = f"{sid}_t0"
            first = [e["point"] for e in _events(events)
                     if e.get("type") == "PointAdded"
                     and (e.get("point") or {}).get("id") == pid]
            assert first and first[0].get("embedding_model"), (
                "premise: capture 1 attests its model")

            with mock.patch("tortoise.embeddings.encode_batch_for_store",
                            _embedder_down):
                sdk.capture_session(conv, session_id=sid)

            recs = [e["point"] for e in _events(events)
                    if e.get("type") == "PointAdded"
                    and (e.get("point") or {}).get("id") == pid]
            assert len(recs) >= 2, "expected a second PointAdded"
            assert recs[-1].get("embedding") is not None, (
                "premise: the preserved vector is journalled")
            assert "embedding_model" not in recs[-1], (
                "a PRESERVED vector was attested to the ACTIVE model — the "
                "record claims an origin it cannot know (round-4 P2)")
            assert "embedding_text_hash" not in recs[-1]
        finally:
            sdk.close()


# ── 11. round-5 review findings — regression pins ────────────────────────
#
# Round 4 made the verbatim marker a NODE property, which put it on the same
# ground as `embedding`/`content_hash` for two cases the marker itself created:
# a tenant could forge it (and its sibling `embedding_preserved`) through props,
# and the `is_recreate` wipe cleared `embedding`/`content_hash` but NOT the new
# marker. A third finding is the residue of the #5046 class that surfaces on a
# path #5004 DOES cover: `update_point(embedding=…)` wrote a raw caller vector
# without the marker, so a later promote narrowed it.

def test_a_recreated_point_does_not_inherit_the_verbatim_marker(tmp_path):
    """A delete → same-id re-create must not leak the dead incarnation's marker.

    Round-5 P1: `embedding_verbatim` joins `embedding`/`content_hash` as a
    derived field the `is_recreate` wipe must clear. Its SET clause uses
    `CASE … ELSE n.embedding_verbatim`, which PRESERVES the old value when the
    re-creation carries none — so the rebuilt node held a property the live node
    did not (the #330/#3312 parity break), and the leaked `true` would make the
    new node store its vector raw and skip the R1 attestation.
    """
    events = tmp_path / "events"
    events.mkdir()
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "recreate.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            pid = sdk.create_point("statement", "first incarnation",
                                   embedding=[0.1] * _DIM).get("id")
            props = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            assert props.get("embedding_verbatim") is True, (
                "premise: the caller-vectored point carries the marker")

            sdk.delete_point(pid)
            # Same id, now a SERVER vector — the recreate must be marker-free.
            sdk.create_point("statement", "second incarnation", id=pid)
            live_props = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            assert not live_props.get("embedding_verbatim"), (
                "premise: the live re-create carries no marker")

            sdk._get_proj().rebuild_all(str(events))
            re_props = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            assert not re_props.get("embedding_verbatim"), (
                "the re-created node inherited the DEAD incarnation's "
                "embedding_verbatim marker — an is_recreate-wipe gap "
                "(round-5 P1)")
        finally:
            sdk.close()


def test_update_point_with_a_caller_vector_marks_the_node(tmp_path):
    """`update_point(embedding=…)` must mark the node verbatim too.

    Round-5 P2: the marker was written only by `create_point`, so a caller
    vector supplied through `update_point` (stored RAW by `n += $props`) had no
    marker — and a later `promote_point` re-emit then let the replay narrow it
    through `vecf32` (`0.3` -> `0.30000001192092896`). The break surfaces on a
    path #5004 does claim to cover, so it is fixed here rather than deferred to
    #5046 (which covers the `PointRevised` record itself).
    """
    events = tmp_path / "events"
    events.mkdir()
    caller_vec = [0.3] * _DIM
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "upd.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            pid = sdk.create_point("statement", "update vector probe").get("id")
            sdk.update_point(pid, embedding=caller_vec)
            assert _vector(sdk, pid) == caller_vec, (
                "premise: the live update took the caller vector")
            sdk.promote_point(pid)
            live = _vector(sdk, pid)

            with mock.patch(_EMBED_PATCH, _embed_b):
                sdk._get_proj().rebuild_all(str(events))
            assert _vector(sdk, pid) == live, (
                "a caller vector supplied via `update_point` was narrowed to "
                "float32 by the promote re-emit (round-5 P2)")
        finally:
            sdk.close()


# ── 12. round-6 review findings — regression pins ────────────────────────

def test_a_cleared_turn_vector_is_cleared_on_replay_too(tmp_path, monkeypatch):
    """An owned `None` is a CLEAR, not merely a refusal to write.

    Round-6 P1: the round-3 fix stopped an owned `None` being RECOMPUTED, but
    the SET clause's `WHEN $embedding IS NULL THEN n.embedding` PRESERVED
    whatever an EARLIER record had set. So for a turn re-captured with CHANGED
    content while the embedder was down — where the live write deliberately
    writes NULL, because a vector for text no longer on the node is the
    dense-leg lie — the replay resurrected capture 1's vector. On the
    highest-volume producer, under the SAME embedder.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    events = tmp_path / "events"
    events.mkdir()
    sid = "resp_clear_probe"

    def _embedder_down(*_a, **_k):
        raise RuntimeError("embedder unavailable (simulated)")

    with mock.patch(_EMBED_PATCH, _embed_a), mock.patch(_BATCH_PATCH, _BATCH_A):
        sdk = TortoiseSDK(str(tmp_path / "clear.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            sdk.capture_session(
                [{"role": "user", "content": "ORIGINAL turn text"}],
                session_id=sid)
            pid = f"{sid}_t0"
            assert _vector(sdk, pid) is not None, "premise: capture 1 embedded"

            # CHANGED content + embedder down => the live write CLEARS.
            with mock.patch(_BATCH_PATCH, _embedder_down):
                sdk.capture_session(
                    [{"role": "user", "content": "a DIFFERENT replacement"}],
                    session_id=sid)
            assert _vector(sdk, pid) is None, (
                "premise: the live write clears the vector for changed text")

            sdk._get_proj().rebuild_all(str(events))
            assert _vector(sdk, pid) is None, (
                "the replay RESURRECTED a vector the live write CLEARED — an "
                "owned `None` must be a clear, not a no-op (round-6 P1)")
        finally:
            sdk.close()


def test_a_wrong_width_caller_vector_is_not_written_on_either_side(tmp_path):
    """The store-width guard must be TWO-sided.

    Round-6 P2: `create_point(embedding=[...])` stored any width verbatim while
    the replay REFUSED a width the index cannot hold — so live held a vector
    and the rebuilt graph did not. Both sides must degrade to the store's
    declared width.
    """
    events = tmp_path / "events"
    events.mkdir()
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "width.db"),
                          event_log_path=str(events / "events.jsonl"))
        proj = sdk._get_proj()
        try:
            with mock.patch.object(type(proj), "required_embedding_dim",
                                   new_callable=mock.PropertyMock,
                                   return_value=_DIM):
                pid = sdk.create_point("statement", "wrong width probe",
                                       embedding=[0.1] * 10).get("id")
                assert _vector(sdk, pid) is None, (
                    "a wrong-width caller vector must not be stored live")
                proj.rebuild_all(str(events))
                assert _vector(sdk, pid) is None, (
                    "live and replay disagree on a wrong-width caller vector")
        finally:
            sdk.close()


def test_a_graph_only_caller_vectored_point_keeps_its_raw_vector_and_marker(tmp_path):
    """A graph-only point's caller-owned vector must survive the tail intact.

    Round-7 P2: the synthetic graph-only event stripped `embedding` AND
    `embedding_verbatim`, so the pass-1b tail restored the vector through
    `vecf32()` (the one form a caller-owned vector must not take) and never
    restored the marker — live had the raw vector + marker, replay had a
    narrowed vector and no marker. Both halves must match.
    """
    events = tmp_path / "events"
    events.mkdir()
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "graphonly.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            pid = sdk.create_point("statement", "graph-only probe",
                                   embedding=[0.1] * _DIM).get("id")
            assert _vector(sdk, pid) == [0.1] * _DIM, (
                "premise: the caller vector is stored raw")
            live_props = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            assert live_props.get("embedding_verbatim") is True

            # Lose the journal: this id becomes GRAPH-ONLY on the next rebuild,
            # so it is reconstructed by the SYNTHETIC event + the pass-1b tail.
            (events / "events.jsonl").write_text("")
            sdk._get_proj().rebuild_all(str(events))

            rows = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set
            assert rows, (
                "premise changed: a graph-only point is no longer restored by "
                "the pre-wipe tail — this pin is now vacuous")
            assert _vector(sdk, pid) == [0.1] * _DIM, (
                "the graph-only tail NARROWED a caller-owned vector to float32 "
                "(`vecf32`) instead of preserving its raw form (round-7 P2)")
            assert rows[0][0].get("embedding_verbatim") is True, (
                "the caller-owned marker was dropped on the graph-only path — "
                "live holds it, replay must too")
        finally:
            sdk.close()


def test_eventapi_rejects_the_server_managed_journal_markers(tmp_path):
    """`EventAPI.add_point` is a producer that skips `_sanitize_props`.

    Round-7 P3: it is the one journal producer with no props boundary, so
    `add_point(..., embedding_verbatim=True)` forged the marker onto the node
    and `add_point(..., embedding_preserved=True)` silenced the R1 attestation
    on a creating record. Reject both, as the SDK and MCP boundaries do.
    """
    from tortoise.api import EventAPI, provenance
    from tortoise.log import EventLog

    events = tmp_path / "events"
    events.mkdir()
    log = EventLog(events / "events.jsonl")
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "api-forge.db"))
        try:
            api = EventAPI(log, initiated_by="extractor",
                           projection=sdk._get_proj())
            for key in ("embedding_verbatim", "embedding_preserved",
                        "embedding_model", "embedding_revision",
                        "embedding_text_hash"):
                with pytest.raises(ValueError, match="server-managed"):
                    api.add_point("forged marker probe",
                                  provenance("src:1", [0, 4], "q"),
                                  **{key: True})
        finally:
            sdk.close()


def test_update_entity_point_branch_is_a_declared_exemption(tmp_path):
    """`_update_entity`'s Point branch journals NO embedding record at all.

    Round-7 P1 (filed on the pre-existing **#4094**): that branch applies caller
    props with a live `SET n += $p` and emits only the annotator dims — a
    deliberate, code-recorded scope cut. So a caller-supplied `embedding`
    reaches the node with no journal record for it anywhere, and the rebuild
    falls back to the creation vector. #5004 does not cover this path: widening
    it would reverse a recorded decision. This pin declares the gap so the next
    lane sees it was found and routed, not missed. **INVERT it when #4094
    lands.**

    Round-11 addendum — the MARKER dimension. Unlike the vector, the marker
    did not exist before #5004. `_update_entity` now sets it (so a LATER,
    *journaled* `promote_point` re-emit restores the caller vector RAW instead
    of narrowing it through `vecf32` — see
    `test_update_entity_caller_vector_rides_a_re_emit_verbatim`), but the
    branch still journals no embedding line, so a rebuild does not reproduce
    it. Both halves are asserted below: the vector falls back to the creation
    value, and the marker is live-only. The alternative — leaving the vector
    unmarked — trades this declared marker gap for an UNdeclared, byte-level
    vector divergence on a record that IS journaled, which is worse.
    """
    events = tmp_path / "events"
    events.mkdir()
    caller_vec = [0.3] * _DIM
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "entity.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            pid = sdk.create_point("statement", "entity embedding probe").get("id")
            v_created = _vector(sdk, pid)
            assert v_created is not None

            before_n = len(_events(events))
            sdk.update_entity(pid, embedding=caller_vec)
            assert _vector(sdk, pid) == caller_vec, (
                "exemption premise changed: the live _update_entity no longer "
                "takes the caller embedding — #4094 may be fixed; INVERT this "
                "pin")
            # Only the records the UPDATE emitted matter — the creation record
            # legitimately carries the point's original vector.
            new_records = _events(events)[before_n:]
            assert not any(
                (r.get("point") or {}).get("embedding") is not None
                or r.get("embedding") is not None for r in new_records
            ), (f"exemption premise changed: the update emitted an embedding "
                f"record ({[r.get('type') for r in new_records]}) — #4094 may "
                f"be fixed; INVERT this pin")

            with mock.patch(_EMBED_PATCH, _embed_b):
                sdk._get_proj().rebuild_all(str(events))
            assert _vector(sdk, pid) == v_created, (
                "`_update_entity`'s Point-branch embedding write is now "
                "journalled and replayed — INVERT this pin and drop the #4094 "
                "exemption note")
            # Round-11: the marker is live-only on this path — the branch
            # journals no embedding line, so the rebuild cannot reproduce it.
            # Asserted (not merely commented) so the gap stays visible.
            replayed_props = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            assert replayed_props.get("embedding_verbatim") is None, (
                "the marker now survives this rebuild — INVERT this half of "
                "the pin and narrow the #4094 note to the vector only")
            # …and a NON-Point label must never receive the Point-only marker
            # (round-11 P2: the shared `props` dict leaked it to every label
            # the loop visits).
            sid = sdk.create_entity("subject", "marker leak probe")["node"]["id"]
            sdk.update_entity(sid, embedding=caller_vec)
            subj = sdk._get_proj().g.query(
                "MATCH (n:Subject {id:$id}) RETURN properties(n)",
                params={"id": sid}).result_set[0][0]
            assert subj.get("embedding_verbatim") is None, (
                "`embedding_verbatim` is a Point-only property and leaked onto "
                "a Subject (round-11)")
        finally:
            sdk.close()


def test_update_entity_caller_vector_rides_a_re_emit_verbatim(tmp_path):
    """#5004 round-10: a caller vector through `_update_entity` must not be
    narrowed by a LATER re-emit.

    `_update_entity`'s Point branch accepts a caller `embedding` (the recorded
    PR #3018 decision — `_sanitize_props` deliberately keeps it) and writes it
    RAW via `SET n += $p`. It journals no embedding line of its own — the
    **#4094** exemption, pinned above. But the marker has to ride the NODE
    regardless, because a *different* record journals it: `promote_point`
    emits `get_point(pid)` as its snapshot. Without the marker the snapshot
    carried the raw vector UNMARKED, `_upsert_point_props` took its `vecf32`
    arm, and the replay returned `0.30000001192092896` where live held `0.3`.
    That is the same-journal-different-graph class #5004 exists to remove, so
    it is fixed here even though the *journalling* gap stays with #4094.
    """
    events = tmp_path / "events"
    events.mkdir()
    caller_vec = [0.3] * _DIM
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "entityreemit.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            pid = sdk.create_point("statement", "entity re-emit probe").get("id")
            sdk.update_entity(pid, embedding=caller_vec)
            sdk.promote_point(pid)

            live = _vector(sdk, pid)
            live_props = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            assert live == caller_vec, "premise: the caller vector is live"
            assert live_props.get("embedding_verbatim") is True, (
                "`_update_entity` stored a caller vector without marking it — "
                "a later re-emit will journal it unmarked (round-10)")

            # Different embedder on the replay: only a VERBATIM restore can
            # reproduce `0.3` exactly; a `vecf32` narrowing cannot.
            with mock.patch(_EMBED_PATCH, _embed_b):
                sdk._get_proj().rebuild_all(str(events))
            replayed = _vector(sdk, pid)
            replayed_props = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            assert replayed == caller_vec, (
                f"the replay narrowed a caller-owned vector: {replayed[:1]} "
                f"!= {caller_vec[:1]}")
            assert replayed_props.get("embedding_verbatim") is True
        finally:
            sdk.close()


def test_fresh_mitigate_operator_journals_an_owned_none_and_replays_it(tmp_path):
    """#5037: the fresh `mitigate_operator` path must not INVENT a vector.

    Round-2 filed **#5037** because the mitigation Point is created by a live
    raw write and then journaled from `get_point`, whose snapshot carried no
    `embedding` key at all — so a strip-era-less replay took the recompute arm
    and invented a vector the live graph had none of. With `_emit_event`
    forcing the key present as an owned `None`, the record now SPEAKS about
    the field and the replay's `$embedding_clear` arm writes NULL: parity on
    both the vector and the marker.

    This is #5037's coverage on the FRESH path. The idempotent re-mitigation
    branch is the `PointRevised` shape, which stays with **#5046**.
    """
    events = tmp_path / "events"
    events.mkdir()
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "mitig.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            a = sdk.create_point("statement", "mitigation probe A").get("id")
            b = sdk.create_point("statement", "mitigation probe B").get("id")
            op = sdk.create_operator("NAND", a, [b]).get("id")
            mid = sdk.mitigate_operator(op, "because reasons", 0.5).get("id")
            assert mid, "the mitigation Point has no id"

            record = [
                r for r in _events(events)
                if r.get("type") == "PointAdded"
                and (r.get("point") or {}).get("id") == mid]
            assert record, "the mitigation Point has no PointAdded record"
            snapshot = record[0]["point"]
            assert "embedding" in snapshot, (
                "the mitigation record does not SPEAK about the embedding — a "
                "replay would take the recompute arm and invent a vector "
                "(#5037, presence-is-ownership)")
            assert snapshot["embedding"] is None
            # A re-emit is NOT a creating record: it must not claim an origin
            # for a vector it does not carry (`stamp_journal_embedding` pops
            # the identity keys when `creating=False`).
            assert "embedding_model" not in snapshot
            assert _vector(sdk, mid) is None, "premise: live has no vector"

            with mock.patch(_EMBED_PATCH, _embed_b):
                sdk._get_proj().rebuild_all(str(events))
            assert _vector(sdk, mid) is None, (
                "the replay INVENTED a vector for the mitigation Point — #5037 "
                "is back")
            props = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": mid}).result_set[0][0]
            assert props.get("embedding_verbatim") is None, (
                "the replay marked a vectorless node as caller-owned")
        finally:
            sdk.close()


def test_clearing_the_vector_clears_the_marker_too(tmp_path):
    """`update_point(embedding=None)` must not leave a marker with no vector.

    Round-8 note: `n += $props` removed the vector but left `embedding_verbatim`
    behind, so the node claimed "caller-owned, stored verbatim" with no vector —
    and a rebuild then either dropped the marker (diverging from live) or put it
    back beside a RECOMPUTED vector (the false-verbatim class round 6 closed).

    **LIVE half** asserted below. The **REPLAY half** is a DECLARED EXEMPTION,
    pinned at the end of this test: `PointRevised` is the one producer outside
    the presence-is-ownership rule (#5046) — `_revise_point` writes `n.embedding`
    only when `new_content is not None` and never reads `embedding_verbatim`, so
    the record's owned `null` clear is **not** applied and pass-1a's `PointAdded`
    vector+marker survive the rebuild. That is the #5046 mechanism, NOT something
    this change introduces; the marker dimension is new only because round 8
    added the marker. **INVERT the replay half when #5046 lands.**
    """
    events = tmp_path / "events"
    events.mkdir()
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "clearmark.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            pid = sdk.create_point("statement", "clear the marker",
                                   embedding=[0.2] * _DIM).get("id")
            props = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            assert props.get("embedding_verbatim") is True

            sdk.update_point(pid, embedding=None)
            props = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            assert props.get("embedding") is None, "premise: vector cleared"
            assert props.get("embedding_verbatim") is None, (
                "the verbatim marker survived its vector — the node claims a "
                "form for a vector it does not have (round-8)")

            # ── DECLARED EXEMPTION (regression pin) ──────────────────────────
            # A round-9 reviewer asked for live==replay here. It is NOT equal,
            # and the cause is #5046, not this change: the journal is sufficient
            # (the record carries `embedding: null` + `embedding_verbatim: null`)
            # but `_revise_point` ignores both. Fixing that is #5046's scope —
            # doing it here would silently reverse the recorded decision that
            # exempted `PointRevised` from #5004, which the contradiction test
            # forbids. This pin asserts the CURRENT, documented divergence so it
            # cannot drift unnoticed, and so the exemption is covered by a test.
            sdk._get_proj().rebuild_all(str(events))
            replayed = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            assert replayed.get("embedding") is not None, (
                "#5046 landed? the clear IS replayed now — INVERT this pin")
            assert replayed.get("embedding_verbatim") is True, (
                "#5046 landed? the marker clear IS replayed — INVERT this pin")
        finally:
            sdk.close()


def test_a_stale_promote_after_recreate_is_a_declared_exemption(tmp_path):
    """A `PointPromoted` predating a delete→recreate re-applies DEAD derived state.

    Round-8 P2, filed as **#5068**: pass-1a hoists every creation, and the
    `#2884 A7` hard-delete boundary gates only `BELIEF_PROPS` — explicitly, as a
    recorded #785-parity decision — so the promote's `embedding` **and** the new
    `embedding_verbatim` marker are folded onto the FRESH incarnation. Widening
    that gate would reverse the recorded decision, so #5004 does not; this pin
    declares the residual. **INVERT it when #5068 lands.**
    """
    events = tmp_path / "events"
    events.mkdir()
    with mock.patch(_EMBED_PATCH, _embed_a):
        sdk = TortoiseSDK(str(tmp_path / "staleprom.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            pid = sdk.create_point("statement", "first incarnation",
                                   embedding=[0.1] * _DIM).get("id")
            sdk.promote_point(pid)
            sdk.delete_point(pid)
            sdk.create_point("statement", "second incarnation", id=pid)
            live = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            assert not live.get("embedding_verbatim"), (
                "premise: the live re-creation is server-vectored and marker-free")

            sdk._get_proj().rebuild_all(str(events))
            replay = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN properties(n)",
                params={"id": pid}).result_set[0][0]
            if not replay.get("embedding_verbatim"):
                pytest.fail(
                    "#5068 appears FIXED on this tree — the stale promote no "
                    "longer re-applies the dead incarnation's marker. INVERT "
                    "this pin and drop the exemption note")
            assert live.get("embedding") != replay.get("embedding"), (
                "the vector half changed too — revisit the #5068 exemption note")
        finally:
            sdk.close()
