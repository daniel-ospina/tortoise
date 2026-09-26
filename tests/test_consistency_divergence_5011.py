"""#5011 — divergence detection must be by CONTENT, not by size.

The invariant this file guards (docs/architecture/STORAGE-ARCHITECTURE.md §3):

    derived tables = replay(the journal)

A count check (`log_points == db_points`) cannot see a projection that is WRONG
in content but RIGHT in size — a stale ``status``, a tampered ``content``, a
dropped field. Before this change the tree contained no projection digest at
all (a search for ``projection_hash`` over ``tortoise/`` returned 0 matches);
the field introduced here, ``projection_hash_sha256``, is the recorded graph
baseline.

Doctrine (TEST-DOCTRINE.md, Class B) — every test below answers both questions
in its docstring:

  (1) what value makes this test fail?
  (2) does the fixture contain a row where that value is reachable?

The load-bearing one is ``test_content_wrong_size_right_is_caught_where_count_is_not``:
its own ``delta == 0`` assertion proves the OLD check passes the very fixture
the new one must catch, so the test cannot pass vacuously.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

from tortoise.consistency import (
    _GRAPH_DEFAULTED_PROPS,
    _WRITER_DEFAULTS,
    check_consistency,
    read_projection_state,
    recover_from_log,
)
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection


def _proj(tmp_path, tag: str) -> FalkorProjection:
    # graph_name starts with `test_` so the bulk-wipe guard passes on the
    # docker lane; unique so parallel workers never share a graph.
    name = f"test_5011_{tag}_{uuid4().hex[:8]}"
    return FalkorProjection(os.path.join(str(tmp_path), "5011.db"),
                            graph_name=name)


def _pt(pid: str, content: str, **extra) -> dict:
    p = {"id": pid, "content": content, "kind": "statement",
         "status": "live", "createdAt": "2026-01-01T00:00:00Z"}
    p.update(extra)
    return {"type": "PointAdded", "point": p}


def _seed(proj, log_path: str, events: list[dict]) -> EventLog:
    """Write the journal AND apply it — a faithful (non-diverged) projection."""
    log = EventLog(log_path)
    for ev in events:
        log.append(ev)
        proj.apply(ev)
    return log


@pytest.fixture
def proj(tmp_path):
    p = _proj(tmp_path, "x")
    try:
        yield p
    finally:
        p.close()


# ── the trap: content-wrong, size-right ───────────────────────────────────


def test_content_wrong_size_right_is_caught_where_count_is_not(proj, tmp_path):
    """(1) The value that makes this fail is the tampered ``content`` of ``p2``.
    Under the pre-#5011 count-only check it is INVISIBLE — which is why this
    test asserts ``delta == 0`` on the same fixture: the count check passes
    here, so only the content check can fail.
    (2) Reachable: ``p2`` is mutated in place via raw Cypher (the graph write
    path, no journal record), so a row carrying the bad value exists.
    """
    log_path = str(tmp_path / "trap.jsonl")
    _seed(proj, log_path, [_pt("p1", "first"), _pt("p2", "second")])

    first = check_consistency(log_path, proj)
    assert first["ok"], first
    assert first["adopted"], "pre-existing graph must be adopted on run 1"

    # Mutate ONE point in place: same ids, same node count, wrong content.
    proj.g.query("MATCH (n:Point {id:'p2'}) SET n.content='tampered'")

    result = check_consistency(log_path, proj)
    # The count-only verdict is STILL GREEN on this fixture:
    assert result["delta"] == 0
    assert result["log_points"] == result["db_points"] == 2
    # ...and the content verdict is the only thing that can catch it. The
    # cause is named precisely: the journal did NOT move, the graph did.
    assert result["ok"] is False
    assert result["hash_match"] is False
    assert result["divergence"] == "unrecorded-mutation"
    named = {d["id"]: d["fields"] for d in result["divergent_points"]}
    assert "p2" in named, result["divergent_points"]
    assert "content" in named["p2"], result["divergent_points"]
    assert "p1" not in named, "an untouched point must not be reported"


def test_stale_status_is_caught_by_content(proj, tmp_path):
    """A stale ``status`` (the issue's own example) — same trap, different field.
    (1) The value is ``status='retracted'`` on a live point. (2) The fixture
    holds that row after the in-place write."""
    log_path = str(tmp_path / "status.jsonl")
    _seed(proj, log_path, [_pt("p1", "kept")])
    check_consistency(log_path, proj)  # adopt

    proj.g.query("MATCH (n:Point {id:'p1'}) SET n.status='retracted'")
    result = check_consistency(log_path, proj)
    assert result["delta"] == 0, "size is unchanged — the count check is green"
    assert result["ok"] is False
    assert result["hash_match"] is False
    assert "status" in {f for d in result["divergent_points"] for f in d["fields"]}


def test_divergence_is_content_when_the_projection_folds_a_wrong_value(proj, tmp_path):
    """BOTH sides moved but disagree — a wrong fold, not a stall or a stray write.
    (1) Fails if the cause is misreported as 'lag' or 'unrecorded-mutation'.
    (2) Reachable: the journal advances one revision while the graph folds a
    different value for it."""
    log_path = str(tmp_path / "wrongfold.jsonl")
    _seed(proj, log_path, [_pt("p1", "v1")])
    check_consistency(log_path, proj)  # adopt at seq 1

    EventLog(log_path).append(
        {"type": "PointRevised", "id": "p1", "new_content": "v2"})
    proj.g.query("MATCH (n:Point {id:'p1'}) SET n.content='v2-WRONG'")

    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    assert r["divergence"] == "content", r
    assert "replay" in (r["action"] or "")


def test_missing_point_is_caught_even_when_a_ghost_replaces_it(proj, tmp_path):
    """Content catches a substitution the count cannot: same size, different id.
    (1) Delete ``p2`` and create a ghost in one turn — the count is 2 again.
    (2) Both the deletion and the ghost are reachable graph states."""
    log_path = str(tmp_path / "swap.jsonl")
    _seed(proj, log_path, [_pt("p1", "one"), _pt("p2", "two")])
    check_consistency(log_path, proj)  # adopt

    proj.g.query("MATCH (n:Point {id:'p2'}) DETACH DELETE n")
    proj.g.query("CREATE (:Point {id:'ghost', content:'not in journal'})")

    result = check_consistency(log_path, proj)
    assert result["delta"] == 0, "2 in, 2 out — a count check sees nothing"
    assert result["ok"] is False
    named = {d["id"] for d in result["divergent_points"]}
    assert {"p2", "ghost"} <= named, result["divergent_points"]


# ── adoption: an existing graph is adopted, never accused ─────────────────


def test_existing_graph_is_adopted_not_reported_diverged(proj, tmp_path):
    """(1) Fails if the first run against a pre-existing graph reports
    ``divergence`` or fails to record a baseline (``adopted`` False).
    (2) Reachable by construction: the fixture has NO projection-state SIDECAR
    next to its journal — exactly the state of every graph in production
    today."""
    log_path = str(tmp_path / "adopt.jsonl")
    _seed(proj, log_path, [_pt("p1", "a"), _pt("p2", "b")])

    assert read_projection_state(log_path) == (None, None), \
        "pre-existing: no baseline yet"

    r1 = check_consistency(log_path, proj)
    assert r1["ok"] is True
    assert r1["divergence"] is None
    assert r1["adopted"] is True, "first healthy run must ADOPT, not accuse"
    assert r1["watermark"] == 2
    assert r1["state_recorded"] is True
    assert r1["state_error"] is None

    stored, err = read_projection_state(log_path)
    assert err is None, err
    assert stored is not None and stored["last_applied_seq"] == 2
    assert stored["content_hash"] == r1["db_hash"]

    r2 = check_consistency(log_path, proj)
    assert r2["adopted"] is False, "adoption is one-time"
    assert r2["ok"] is True and r2["watermark"] == 2


def test_an_inconsistent_graph_is_never_adopted(proj, tmp_path):
    """Adoption must not bless a graph that is already wrong.
    (1) Fails if ``adopted``/state-write happen despite a size mismatch.
    (2) Reachable: the fixture injects a ghost before the first check."""
    log_path = str(tmp_path / "badfirst.jsonl")
    _seed(proj, log_path, [_pt("p1", "only one")])
    proj.g.query("CREATE (:Point {id:'ghost', content:'extra'})")

    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    assert r["adopted"] is False
    assert r["state_recorded"] is False
    assert read_projection_state(log_path)[0] is None, "no baseline for a bad graph"


# ── the watermark ─────────────────────────────────────────────────────────


def test_watermark_advances_with_the_journal(proj, tmp_path):
    """(1) Fails if the watermark is not re-recorded (it would stay at 2) or if
    the lag is miscomputed. (2) Reachable: a third event is appended+applied."""
    log_path = str(tmp_path / "wm.jsonl")
    _seed(proj, log_path, [_pt("p1", "a"), _pt("p2", "b")])
    assert check_consistency(log_path, proj)["watermark"] == 2

    log = EventLog(log_path)
    ev = _pt("p3", "c")
    log.append(ev)
    proj.apply(ev)

    r = check_consistency(log_path, proj)
    assert r["ok"] is True
    assert r["journal_events"] == 3
    assert r["watermark"] == 3, "the watermark must move with the journal"
    assert r["watermark_lag"] == 0


def test_projection_behind_the_journal_is_named_lag(proj, tmp_path):
    """journal moved, graph did NOT — a dropped projection write, not a wipe.
    (1) Fails if the cause collapses to a generic 'content'. (2) Reachable:
    append to the log without applying it."""
    log_path = str(tmp_path / "lag.jsonl")
    _seed(proj, log_path, [_pt("p1", "a")])
    check_consistency(log_path, proj)  # adopt at seq 1

    EventLog(log_path).append(_pt("p2", "never applied"))

    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    assert r["hash_match"] is False
    assert r["divergence"] == "lag", r
    assert r["watermark_lag"] == 1
    assert "re-apply" in (r["action"] or "")


def test_unrecorded_graph_write_is_named_unrecorded_mutation(proj, tmp_path):
    """graph moved, journal did NOT — the #4240 class (a write that bypassed
    the journal). (1) Fails if the cause is reported as a generic 'content'.
    (2) Reachable: an in-place write with no journal record."""
    log_path = str(tmp_path / "unrec.jsonl")
    _seed(proj, log_path, [_pt("p1", "a")])
    check_consistency(log_path, proj)  # adopt at seq 1

    proj.g.query("MATCH (n:Point {id:'p1'}) SET n.content='written raw'")

    r = check_consistency(log_path, proj)
    assert r["delta"] == 0, "size unchanged — only content moved"
    assert r["divergence"] == "unrecorded-mutation", r
    assert "#4240" in (r["action"] or "")


# ── coverage gaps are named, never silently hashed away ───────────────────


def test_uncarried_journal_content_is_named_not_hidden(proj, tmp_path):
    """``tags`` is journal content the projection writer drops (#2897).
    (1) Fails if the loss is reported as an empty list — i.e. hidden.
    (2) Reachable: the fixture's point carries ``tags``."""
    log_path = str(tmp_path / "tags.jsonl")
    _seed(proj, log_path, [_pt("p1", "tagged", tags=["alpha", "beta"])])

    r = check_consistency(log_path, proj)
    assert "tags" in r["uncarried_journal_fields"], r
    # The gap is reported as a COVERAGE GAP; it does not masquerade as a
    # content divergence (which would make the check unusable until #2897).
    assert r["ok"] is True
    assert r["divergence"] is None


def test_uncarried_list_is_empty_when_the_journal_has_no_such_field(proj, tmp_path):
    """The negative control for the test above: without the field, the report
    must be empty — otherwise ``uncarried_journal_fields`` is a constant.
    (1) Fails if it is non-empty. (2) The fixture has no ``tags`` row."""
    log_path = str(tmp_path / "notags.jsonl")
    _seed(proj, log_path, [_pt("p1", "plain")])
    r = check_consistency(log_path, proj)
    assert r["uncarried_journal_fields"] == []


# ── the embedding: compared when journalled, so an embedder swap is caught ─


def test_journalled_embedding_is_compared_and_a_changed_one_is_caught(proj, tmp_path):
    """The issue's *"catch the embedding divergence for free"* case.
    (1) The value is the mutated vector; the hash must notice it. (2)
    Reachable: the journal carries a store-width vector, and it is then
    rewritten in the graph with a different one of the same width."""
    width = proj.required_embedding_dim
    if not width:
        pytest.skip("store reports no embedding width")
    log_path = str(tmp_path / "vec.jsonl")
    zero = [0.0] * width
    _seed(proj, log_path, [_pt("p1", "vec", embedding=zero)])

    clean = check_consistency(log_path, proj)
    assert clean["embedding_compared"] == 1, "a journalled vector must be compared"
    assert clean["ok"] is True

    proj.g.query(
        "MATCH (n:Point {id:'p1'}) SET n.embedding=vecf32($v)",
        {"v": [1.0] * width},
    )
    r = check_consistency(log_path, proj)
    assert r["ok"] is False and r["hash_match"] is False
    assert "embedding" in {f for d in r["divergent_points"] for f in d["fields"]}


# ── the baseline is a sidecar, not a graph node ───────────────────────────


def test_projection_state_is_a_sidecar_and_never_makes_a_graph_non_empty(
        proj, tmp_path):
    """The watermark/baseline must not be GRAPH data, or every node-counting
    surface (the DR dump's data-node count, ``migrate_db``, the recovery
    emptiness test) would need an exclusion the export skip-set
    (``hosted_api.py``) does not provide.
    (1) Fails if the state is a graph node (the emptied graph reads non-empty
    and recovery is refused), or if the sidecar is picked up as a second
    journal by ``recover_from_log`` (recovery refused as ambiguous).
    (2) Reachable: adopt (writes the sidecar), then delete every Point.
    """
    events_dir = tmp_path / "dr"
    events_dir.mkdir()
    log_path = str(events_dir / "events.jsonl")
    _seed(proj, log_path, [_pt("p1", "a"), _pt("p2", "b")])
    check_consistency(log_path, proj)  # adopts -> writes the sidecar

    # The state is a file NEXT TO the journal, and it is not a `.jsonl`.
    assert os.path.exists(log_path + ".projection-state.json")
    assert read_projection_state(log_path)[0] is not None

    # Wipe every node: the graph is empty, and the sidecar does not change that.
    proj.g.query("MATCH (n) DETACH DELETE n")
    rows = proj.g.query("MATCH (n) RETURN count(n)").result_set
    assert rows[0][0] == 0

    result = recover_from_log(str(events_dir), proj)
    assert result["recovered"] is True, result
    assert result["db_points"] == 2, result


# ── writer defaults: the fail-open that hid the issue's own example ───────


def test_writer_default_is_not_a_divergence_but_a_non_default_is(proj, tmp_path):
    """``content``/``status`` are set UNCONDITIONALLY by the writer, so a
    journal that omits them still expects the DEFAULT — and any other value is
    the graph holding something the journal never authored.
    (1) Fails in both directions: a false positive if the faithful default is
    reported, a false NEGATIVE (fail-open) if the tampered one is not.
    (2) Reachable: ``_pt`` always carries both keys, so drop them from the
    payload, then tamper the graph's ``content`` only.
    """
    assert {"content", "status"} == set(_WRITER_DEFAULTS), _WRITER_DEFAULTS
    log_path = str(tmp_path / "defaults.jsonl")
    bare = _pt("p1", "ignored")
    bare["point"].pop("content")
    bare["point"].pop("status")
    _seed(proj, log_path, [bare])

    # A faithful replay put the writer's defaults there -> NO divergence.
    clean = check_consistency(log_path, proj)
    assert clean["ok"] is True, clean["divergent_points"]
    assert clean["divergence"] is None

    # The graph now holds a value the journal never authored. With the journal
    # still omitting the key, this is exactly the fail-open the count check had.
    proj.g.query("MATCH (n:Point {id:'p1'}) SET n.content='never authored'")
    r = check_consistency(log_path, proj)
    assert r["ok"] is False, "a non-default graph value must never pass"
    assert "content" in {f for d in r["divergent_points"] for f in d["fields"]}


def test_created_at_is_a_declared_undecidable_key(proj, tmp_path):
    """`createdAt` is a writer default whose value is NOT deterministic
    (`coalesce($ca, n.createdAt, $now)`), so a journal that omits it cannot state
    an expectation — the graph's value must be skipped, and the skip REPORTED.
    (1) Fails as a false positive when the journal omits `createdAt` (the graph
    then holds the `$now` fallback); fails as an invisible bound if the skip is
    not reported.
    (2) Reachable: a payload with no `createdAt` key at all.
    """
    assert frozenset({"createdAt", "expiredAt"}) == _GRAPH_DEFAULTED_PROPS
    log_path = str(tmp_path / "ca.jsonl")
    pt = _pt("c1", "a")
    pt["point"].pop("createdAt")
    _seed(proj, log_path, [pt])
    # The graph holds the writer's wall-clock fallback, which the journal cannot
    # state -> healthy, and the declared bound is visible.
    clean = check_consistency(log_path, proj)
    assert clean["ok"] is True, clean["divergent_points"]
    assert "createdAt" in clean["excluded_fields"], clean["excluded_fields"]

    # ...and when the journal DOES carry it, it is compared like anything else.
    log_path2 = str(tmp_path / "ca2.jsonl")
    _seed(proj, log_path2, [_pt("c2", "a")])
    check_consistency(log_path2, proj)
    proj.g.query("MATCH (n:Point {id:'c2'}) SET n.createdAt='1999-01-01T00:00:00Z'")
    r = check_consistency(log_path2, proj)
    assert r["ok"] is False
    assert "createdAt" in {f for d in r["divergent_points"] for f in d["fields"]}


# ── the graph-only baseline covers the embedding ──────────────────────────


def test_embedding_only_tamper_moves_the_baseline(proj, tmp_path):
    """A graph whose ONLY change is a rewritten vector must still be caught: the
    recorded baseline (``db_hash``) includes the embedding even though the
    field-aware verdict compares it conditionally.
    (1) The value is the mutated vector; if ``db_hash`` excluded the embedding
    this would be reported as healthy.
    (2) Reachable: a store-width vector written in-graph with no journal record.
    """
    width = proj.required_embedding_dim
    if not width:
        pytest.skip("store reports no embedding width")
    log_path = str(tmp_path / "vecbase.jsonl")
    _seed(proj, log_path, [_pt("p1", "a", embedding=[0.0] * width)])
    clean = check_consistency(log_path, proj)
    assert clean["ok"] is True

    proj.g.query(
        "MATCH (n:Point {id:'p1'}) SET n.embedding=vecf32($v)",
        {"v": [0.5] * width},
    )
    r = check_consistency(log_path, proj)
    assert r["delta"] == 0
    assert r["ok"] is False
    assert r["db_hash"] != clean["db_hash"], "the baseline must move"
    assert r["divergence"] == "unrecorded-mutation", r


def test_float32_rounding_makes_a_faithful_replay_compare_equal(proj, tmp_path):
    """The journal carries the encoder's float64 while the node stores
    ``vecf32`` — so an exact comparison of a FAITHFUL replay fails on mantissa
    bits. (1) Fails with a false positive if the two sides are not compared at
    the stored width. (2) Reachable: a value that is not representable in
    float32 (0.1) is what makes the difference.
    """
    width = proj.required_embedding_dim
    if not width:
        pytest.skip("store reports no embedding width")
    log_path = str(tmp_path / "f32.jsonl")
    vec = [0.1] * width
    _seed(proj, log_path, [_pt("p1", "a", embedding=vec)])
    r = check_consistency(log_path, proj)
    assert r["embedding_compared"] == 1
    assert r["ok"] is True, r["divergent_points"]


# ── the count itself must not fail open ───────────────────────────────────


def test_duplicate_ids_do_not_collapse_the_node_count(proj, tmp_path):
    """``db_points`` must come from the GRAPH's count, not from
    ``len(by_id)``: two nodes sharing an id collapse in the dict, so a
    dict-derived count would report the same size and pass.
    (1) The values are the duplicate node and the resulting ``delta``; the
    old ``len(graph_by_id)`` path reads 1 and reports ``delta == 0``.
    (2) Reachable: a raw Cypher duplicate of an existing id.
    """
    log_path = str(tmp_path / "dup.jsonl")
    _seed(proj, log_path, [_pt("p1", "a")])
    check_consistency(log_path, proj)

    proj.g.query("CREATE (:Point {id:'p1', content:'duplicate id'})")
    r = check_consistency(log_path, proj)
    assert r["log_points"] == 1
    assert r["db_points"] == 2, "a collapsed dict read would say 1"
    assert r["delta"] == -1
    assert r["ok"] is False


# ── the state file is untrusted input ─────────────────────────────────────


def test_unreadable_baseline_is_reported_not_treated_as_divergence(
        proj, tmp_path):
    """A baseline that exists but cannot be parsed is neither 'never baselined'
    nor a divergence — and it must NOT be silently overwritten (that would
    destroy the only record of the prior graph and adopt on the next run).
    (1) The values are a non-JSON sidecar and a wrong ``format_version``.
    (2) Reachable: the file is written directly, as a corrupt disk would.
    """
    log_path = str(tmp_path / "corrupt.jsonl")
    _seed(proj, log_path, [_pt("p1", "a")])
    check_consistency(log_path, proj)
    sidecar = log_path + ".projection-state.json"

    with open(sidecar, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, "an unreadable baseline is not a divergence"
    assert r["state_error"], r
    assert r["state_recorded"] is False, "never overwrite what we cannot read"

    with open(sidecar, "w", encoding="utf-8") as fh:
        fh.write('{"format_version": 99, "last_applied_seq": 1, '
                 '"projection_hash_sha256": "' + "0" * 64 + '"}')
    r2 = check_consistency(log_path, proj)
    assert r2["state_error"] and "format" in r2["state_error"], r2
    assert r2["state_recorded"] is False


def test_record_state_false_is_read_only(proj, tmp_path):
    """``record_state=False`` must not write a baseline — a caller running the
    check against a store it must not touch (a read replica, a probe) depends
    on it. (1) Fails if the sidecar appears. (2) Reachable: the flag.
    """
    log_path = str(tmp_path / "readonly.jsonl")
    _seed(proj, log_path, [_pt("p1", "a")])
    r = check_consistency(log_path, proj, record_state=False)
    assert r["ok"] is True
    assert r["state_recorded"] is False
    assert not os.path.exists(log_path + ".projection-state.json")


# ── a real producer's shape must not be a false positive ──────────────────


def test_real_producer_payload_shape_is_healthy(proj, tmp_path):
    """The exclusions must be narrow enough that a point with a REAL nested
    payload (``provenance``, an ``operator``, a journalled vector) replays
    clean — a check that fires on every faithful write is a false-positive
    generator, and every exclusion that prevents that is a blind spot. Both
    producer shapes are exercised, because they take different writer branches.
    (1) Fails if any declared translation is wrong (nested
    ``provenance.source_id``/``operator.op_type``/``is_operator``) or if a
    declared writer carve-out is misread as a divergence.
    (2) Reachable: the two payload shapes the SDK/live path actually emits.
    """
    width = proj.required_embedding_dim
    log_path = str(tmp_path / "real.jsonl")

    plain = _pt("plain", "a plain point")
    plain["point"]["provenance"] = {"speaker": "daniel", "source_id": "src-1"}
    # A falsy write-form flag leaves the node property ABSENT while the journal
    # carries an explicit `false` (#5004 round-3) — a declared asymmetry that
    # must not read as a dropped field.
    plain["point"]["embedding_verbatim"] = False
    op_pt = _pt("op", "an operator point")
    op_pt["point"].update({
        "provenance": {"speaker": "daniel", "source_id": "src-2"},
        "operator": {"op_type": "IMPL"},
    })
    if width:
        plain["point"]["embedding"] = [0.25] * width
        # The writer's operator branch refuses the journalled vector outright
        # (`if not op and journalled is not None`): an operator point carries no
        # content, so a stored vector would be unqueryable. That is why the
        # embedding is compared presence-conditionally — a journal-only vector
        # is a declared carve-out, NOT a dropped field.
        op_pt["point"]["embedding"] = [0.25] * width
    _seed(proj, log_path, [plain, op_pt])

    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]
    assert r["divergence"] is None
    assert "embedding" not in r["uncarried_journal_fields"]
    if width:
        assert r["embedding_compared"] == 1, "only the plain point stores one"


def test_speaker_is_compared_when_both_sides_carry_it(proj, tmp_path):
    """``speaker`` is representation-dependent: ``fold`` mirrors
    ``provenance.speaker`` while the writer carries it only when the payload
    did. So it is skipped when only ONE side has it — but a value difference
    between two sides that BOTH carry it is a real divergence and must be
    caught. (1) Fails in both directions (a false positive on the mirror-only
    shape, a false negative when both sides carry it). (2) Reachable: the
    payload carries a top-level ``speaker`` and the graph is then tampered.
    """
    log_path = str(tmp_path / "speaker.jsonl")
    pt = _pt("p1", "a")
    pt["point"]["provenance"] = {"speaker": "daniel", "source_id": "s1"}
    _seed(proj, log_path, [pt])
    assert check_consistency(log_path, proj)["ok"] is True

    proj.g.query("MATCH (n:Point {id:'p1'}) SET n.speaker='someone else'")
    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    assert "speaker" in {f for d in r["divergent_points"] for f in d["fields"]}


# ── the reference must be the WRITER's replay, not a narrower model of it ──


def test_promotion_events_are_folded_like_the_writer(proj, tmp_path):
    """`fold` (the in-memory point index) has no arm for PointPromoted /
    OperatorPromoted — a documented scope gap — while the GRAPH writer applies
    both. Using `fold` alone reports a healthy graph as diverged on the
    product's core draft→live path.
    (1) Fails if the promotion is not applied to the journal side: with plain
    `fold` this fixture reports `promotedAt`/`reviewed`/`status`.
    (2) Reachable: the two-event shape `sdk.promote_point` emits.
    """
    log_path = str(tmp_path / "promo.jsonl")
    _seed(proj, log_path, [
        _pt("p1", "draft body", status="draft"),
        {"type": "PointPromoted",
         "point": {"id": "p1", "content": "draft body", "status": "live",
                   "reviewed": True, "promotedAt": "2026-02-02T00:00:00Z"}},
    ])
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]
    assert r["divergence"] is None


def test_operator_promotion_only_journal_is_healthy(proj, tmp_path):
    """The capture path where `OperatorPromoted` is the operator's ONLY durable
    record — the graph is built by that same event, so the journal side must be
    too. (1) Fails if the promotion snapshot is not folded (the id then reads as
    a graph-only ghost). (2) Reachable: an OperatorPromoted with no PointAdded.
    """
    log_path = str(tmp_path / "oppromo.jsonl")
    _seed(proj, log_path, [
        {"type": "OperatorPromoted",
         "point": {"id": "op1", "is_operator": True, "op_type": "IMPL",
                   "status": "live", "createdAt": "2026-01-01T00:00:00Z"}},
    ])
    r = check_consistency(log_path, proj)
    assert r["log_points"] == r["db_points"] == 1, r
    assert r["ok"] is True, r["divergent_points"]


def test_operator_content_is_journal_only_by_design(proj, tmp_path):
    """#548: an operator node stores NO `content`/`pointKind`; the journal SEAM
    synthesizes both because the replay writer sets them unconditionally. So on
    an operator they are journal-only BY DESIGN and must not read as a dropped
    field — on any journal, live-built or replay-built.
    (1) Fails (false positive) if the operator carve-out is removed.
    (2) Reachable: the payload the seam emits, on a node with the keys removed.
    """
    log_path = str(tmp_path / "oppt.jsonl")
    pt = _pt("op1", "IMPL(a, b)")
    # The canonical producer shape: operator-ness travels in the NESTED key (a
    # flat `is_operator` is precisely the silent operator→claim conversion
    # `_promotion_point_with_operator` exists to prevent — and that the check
    # correctly reports as a divergence).
    pt["point"]["operator"] = {"op_type": "IMPL"}
    _seed(proj, log_path, [pt])
    assert check_consistency(log_path, proj)["ok"] is True

    # The LIVE shape: the node never had content/pointKind (#548).
    proj.g.query("MATCH (n:Point {id:'op1'}) REMOVE n.content, n.pointKind")
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]
    assert r["divergence"] is None


def test_flat_operator_snapshot_is_caught_as_a_claim_conversion(proj, tmp_path):
    """The mirror of the test above, and the #2256 defect class: a FLAT
    `is_operator` snapshot is upserted by the writer from the NESTED key, so the
    node becomes a plain claim while the journal says operator. The check must
    report it — this is the check earning its keep on a real replay hazard.
    (1) Fails if the operator representation is not compared.
    (2) Reachable: the pre-fix `promote_point` emitter shape.
    """
    log_path = str(tmp_path / "flatop.jsonl")
    pt = _pt("op1", "IMPL(a, b)")
    pt["point"]["is_operator"] = True
    pt["point"]["op_type"] = "IMPL"
    _seed(proj, log_path, [pt])
    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    assert "is_operator" in {f for d in r["divergent_points"] for f in d["fields"]}


def test_journal_side_null_is_absence_not_a_value(proj, tmp_path):
    """The writer cannot persist a null property (`coalesce(...)` for the fixed
    clauses, an explicit `v is None` skip in `_persist_extra_props`), so a
    journal-side `None` means ABSENT — not "the graph is missing a value".
    (1) Fails if a null is compared as a value (`authoredBy`/`validTo`/
    `confidence` all become false-positive fields).
    (2) Reachable: the raw-payload producer (`EventAPI.add_point(**fields)`),
    which journals the caller's dict verbatim.
    """
    log_path = str(tmp_path / "nulls.jsonl")
    pt = _pt("n1", "a")
    pt["point"].update({"confidence": None, "validTo": None, "authoredBy": None})
    _seed(proj, log_path, [pt])
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]


def test_float_round_trip_is_not_a_divergence(proj, tmp_path):
    """The store does not round-trip a float bit-exactly (measured on the docker
    lane: 0.8214927174495666 reads back ...567), so a bit-equal comparison makes
    every float prop a false positive.
    (1) The value is `0.8214927174495666` — chosen because it is NOT one the
    store returns unchanged; with exact comparison this fixture diverges.
    (2) Reachable: `confidence`, which every producer writes.
    """
    log_path = str(tmp_path / "float.jsonl")
    value = 0.8214927174495666
    _seed(proj, log_path, [_pt("f1", "a", confidence=value)])
    read_back = proj.g.query(
        "MATCH (n:Point {id:'f1'}) RETURN n.confidence").result_set[0][0]
    assert read_back != value, "fixture must exercise a lossy round-trip"
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]


def test_ep_owned_props_are_excluded_and_reported(proj, tmp_path):
    """EP-owned runtime state (`ep_dirty`, `ep_alpha`, …) is written live by
    ep.py/dream.py with NO journal record, so the journal can never state its
    value. It must be excluded — and NAMED, so the un-modelled surface is
    visible on a green run.
    (1) Fails as a false positive if they are compared; fails as an invisible
    blind spot if `excluded_fields` does not list them.
    (2) Reachable: the two Cypher writes the EP paths perform.
    """
    log_path = str(tmp_path / "ep.jsonl")
    _seed(proj, log_path, [_pt("e1", "a")])
    check_consistency(log_path, proj)

    proj.g.query("MATCH (n:Point {id:'e1'}) SET n.ep_dirty=true, "
                 "n.ep_dirty_at='2026-03-03T00:00:00Z', n.ep_alpha=1.5")
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]
    assert {"ep_dirty", "ep_dirty_at", "ep_alpha"} <= set(r["excluded_fields"]), r


def test_a_journalled_vector_the_graph_lost_is_a_divergence(proj, tmp_path):
    """The embedding comparison is DIRECTION-SENSITIVE: a graph-only vector is
    faithful (the pre-#5004 recompute path for a strip-era journal), but a
    JOURNAL-only vector is a refusals-only case. This is the exact hole the
    presence-conditional comparison left: the graph loses a vector the journal
    recorded, at the store's own width, on a plain point — a divergence.
    (1) Fails if a one-sided vector is skipped in both directions.
    (2) Reachable: `REMOVE n.embedding` on a journalled vector.
    """
    width = proj.required_embedding_dim
    if not width:
        pytest.skip("store reports no embedding width")
    log_path = str(tmp_path / "embgone.jsonl")
    _seed(proj, log_path, [_pt("v1", "a", embedding=[0.25] * width)])
    assert check_consistency(log_path, proj)["ok"] is True

    proj.g.query("MATCH (n:Point {id:'v1'}) REMOVE n.embedding")
    r = check_consistency(log_path, proj)
    assert r["delta"] == 0, "the size check cannot see this"
    assert r["ok"] is False
    assert "embedding" in {f for d in r["divergent_points"] for f in d["fields"]}


def test_a_declared_vector_refusal_is_not_a_divergence(proj, tmp_path):
    """The other direction of the same rule: the writer DECLARES a refusal for
    an operator point (#5004, no content to rank), so a journal-only vector there
    is reported as a declared bound, not a divergence.
    (1) Fails if the refusal is treated as a lost field.
    (2) Reachable: an operator whose payload carries a vector.
    """
    width = proj.required_embedding_dim
    if not width:
        pytest.skip("store reports no embedding width")
    log_path = str(tmp_path / "opvec.jsonl")
    pt = _pt("op1", "IMPL(a)")
    pt["point"]["operator"] = {"op_type": "IMPL"}
    pt["point"]["embedding"] = [0.25] * width
    _seed(proj, log_path, [pt])
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]
    assert "embedding" in r["one_sided_fields"], r


def test_the_baseline_catches_a_skipped_field_the_comparison_tolerates(
        proj, tmp_path):
    """The baseline's load-bearing case: a field the comparison DECLARES it
    cannot decide. A journal with no `embedding` key is the pre-#5004
    strip-era shape, for which the writer RECOMPUTES a vector — so the graph
    legitimately holds one, and the comparison tolerates a graph-only vector.
    A later rewrite of that vector with no journal advance is therefore visible
    ONLY to the graph-only fingerprint.
    (1) Fails if `graph_moved and not journal_moved` is not part of `ok`
    (`hash_match` stays True here by design).
    (2) Reachable: a payload with no `embedding` key, then an in-place rewrite.
    """
    width = proj.required_embedding_dim
    if not width:
        pytest.skip("store reports no embedding width")
    log_path = str(tmp_path / "baseline.jsonl")
    pt = _pt("b1", "a")
    assert "embedding" not in pt["point"]
    _seed(proj, log_path, [pt])
    clean = check_consistency(log_path, proj)
    assert clean["ok"] is True, clean["divergent_points"]
    assert clean["state_recorded"] is True
    assert clean["embedding_compared"] == 0, "the journal states no vector"

    proj.g.query("MATCH (n:Point {id:'b1'}) SET n.embedding=vecf32($v)",
                 {"v": [0.5] * width})
    r = check_consistency(log_path, proj)
    assert r["hash_match"] is True, "the comparison tolerates a graph-only vector"
    assert r["ok"] is False, "the graph moved and the journal did not"
    assert r["divergence"] == "unrecorded-mutation", r
    assert r["state_recorded"] is False, "a bad graph is never re-baselined"


def test_uncarried_is_per_point_not_per_key(proj, tmp_path):
    """A list-valued key on ONE point must not suppress the comparison of a
    same-named scalar on ANOTHER — the first draft reduced the skip to a global
    key set, which made one point's list a fleet-wide blind spot.
    (1) Fails if the scalar's tamper is not reported.
    (2) Reachable: two points sharing a key name with different value shapes.
    """
    log_path = str(tmp_path / "perpoint.jsonl")
    _seed(proj, log_path, [
        _pt("a", "one", custom_extra=["x"]),
        _pt("b", "two", custom_extra="scalar"),
    ])
    proj.g.query("MATCH (n:Point {id:'b'}) SET n.custom_extra='TAMPERED'")
    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    named = {d["id"]: d["fields"] for d in r["divergent_points"]}
    assert "custom_extra" in named.get("b", []), r["divergent_points"]


def test_map_valued_prop_is_reported_as_uncarried_not_diverged(proj, tmp_path):
    """FalkorDB rejects a map as a node property (`_is_persistable_prop_value`),
    so the writer filters it — the projection PROVABLY cannot hold it, and a
    divergence there could never be repaired by a replay. Report the loss.
    (1) Fails if it is reported as a divergence (unrepairable red) or hidden.
    (2) Reachable: `EventAPI.add_point(..., meta={...})`.
    """
    log_path = str(tmp_path / "map.jsonl")
    _seed(proj, log_path, [_pt("m1", "a", meta={"nested": {"a": 1}})])
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]
    assert "meta" in r["uncarried_journal_fields"], r


def test_one_sided_parity_field_is_reported_not_flagged(proj, tmp_path):
    """`fold` mirrors `provenance.speaker` while the writer carries it only when
    the payload did — so a one-sided `speaker` is an asymmetry, not content the
    journal authored. It must be REPORTED (not silently dropped) and must not
    fail the run. (1) Fails if it is flagged (false positive) or invisible.
    (2) Reachable: the ordinary nested-provenance payload.
    """
    log_path = str(tmp_path / "onesided.jsonl")
    pt = _pt("p1", "a")
    pt["point"]["provenance"] = {"speaker": "daniel", "source_id": "s1"}
    _seed(proj, log_path, [pt])
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]
    assert "speaker" in r["one_sided_fields"], r
    assert "speaker" in r["one_sided_fields"]["speaker"], r["one_sided_fields"]


def test_adopted_is_true_for_a_read_only_first_run(proj, tmp_path):
    """Adoption is a property of the RUN, not of the permission to write the
    baseline: a read-only probe of a pre-existing graph is still its first
    healthy sighting. (1) Fails if `adopted` is computed inside the
    `record_state` guard. (2) Reachable: `record_state=False`.
    """
    log_path = str(tmp_path / "adoptro.jsonl")
    _seed(proj, log_path, [_pt("p1", "a")])
    r = check_consistency(log_path, proj, record_state=False)
    assert r["ok"] is True
    assert r["adopted"] is True
    assert r["state_recorded"] is False
    assert not os.path.exists(log_path + ".projection-state.json")


# ── the terminalizing lifecycle events (the writer's repair path folds them) ──


@pytest.mark.parametrize("terminal", [
    {"type": "PointRetracted", "id": "x"},
    {"type": "PointSuperseded", "id": "x", "new_id": "y",
     "valid_to": "2026-02-01T00:00:00Z", "expired_at": "2026-02-01T00:00:00Z"},
    {"type": "PointInvalidated", "id": "x",
     "valid_to": "2026-02-01T00:00:00Z", "expired_at": "2026-02-01T00:00:00Z"},
])
def test_terminalizing_events_are_folded_like_the_writer(proj, tmp_path, terminal):
    """`fold` has no arm for the four point-lifecycle types, but the writer's
    repair path (`rebuild_all` — what `recover_from_log` / `tortoise rebuild`
    run) folds them: status/validity stamps AND the `decay_clause` belief decay
    (`confidence=0.5`, `posterior_alpha/beta=1.0`). A reference built on `fold`
    alone reports the writer's OWN graph as diverged.
    (1) Fails if the terminalizer is not folded on the journal side: with plain
    `fold` this fixture reports `confidence` (retract) or
    `confidence`/`status`/`validTo` (supersede) / `validTo` (invalidate).
    (2) Reachable: the graph is produced by `rebuild_all` over the same events —
    it provably IS `replay(journal)`.
    """
    events_dir = tmp_path / terminal["type"]
    events_dir.mkdir()
    log_path = str(events_dir / "events.jsonl")
    _seed(proj, log_path, [_pt("x", "a", confidence=0.9), terminal])

    # Rebuild from the journal with the writer's own repair path: the graph is
    # then `replay(journal)` by construction, so a red verdict is the check's bug.
    proj.query("MATCH (n) DETACH DELETE n")
    proj.rebuild_all(str(events_dir))

    r = check_consistency(log_path, proj)
    assert r["ok"] is True, (terminal["type"], r["divergent_points"])


def test_a_tamper_of_the_restored_validity_fields_is_caught(proj, tmp_path):
    """`outdated`/`expiredAt` ARE restored from the journal by the
    PointInvalidated fold, so they must be COMPARED — excluding them on the
    strength of `_POINT_DENY` (whose question is about the passthrough, not about
    journal-derivability) would be a blind spot with a false reason.
    (1) Fails if the tamper is invisible. (2) Reachable: replay + in-place SET.
    """
    events_dir = tmp_path / "inv"
    events_dir.mkdir()
    log_path = str(events_dir / "events.jsonl")
    _seed(proj, log_path, [
        _pt("x", "a"),
        {"type": "PointInvalidated", "id": "x",
         "valid_to": "2026-02-01T00:00:00Z",
         "expired_at": "2026-02-01T00:00:00Z"},
    ])
    proj.query("MATCH (n) DETACH DELETE n")
    proj.rebuild_all(str(events_dir))
    assert check_consistency(log_path, proj)["ok"] is True

    proj.g.query("MATCH (n:Point {id:'x'}) SET n.outdated=false, "
                 "n.expiredAt='1999-01-01T00:00:00Z'")
    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    named = {f for d in r["divergent_points"] for f in d["fields"]}
    assert {"outdated", "expiredAt"} <= named, r["divergent_points"]


def test_a_changed_confidence_record_is_compared(proj, tmp_path):
    """The belief half of a terminalizing write is folded from
    `ConfidenceChanged` (BELIEF_PROPS), so the belief props that record states
    are journal-derivable and must be compared — unlike the EP-only runtime keys
    (`ep_alpha`, `c_cal`) that no record states.
    (1) Fails if the whole `_POINT_DENY` list is excluded wholesale.
    (2) Reachable: the ConfidenceChanged payload the SDK emits.
    """
    log_path = str(tmp_path / "belief.jsonl")
    _seed(proj, log_path, [
        _pt("x", "a", confidence=0.5),
        {"type": "ConfidenceChanged", "id": "x", "confidence": 0.8,
         "posterior_alpha": 2.0, "posterior_beta": 1.0},
    ])
    assert check_consistency(log_path, proj)["ok"] is True, "faithful write"

    proj.g.query("MATCH (n:Point {id:'x'}) SET n.posterior_alpha=99.0")
    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    assert "posterior_alpha" in {f for d in r["divergent_points"]
                                for f in d["fields"]}


def test_envelope_and_edge_carrier_keys_are_not_phantom_fields(proj, tmp_path):
    """A real producer (`EventAPI.add_point(**fields)`) puts `_META_KEYS` on the
    payload — envelope metadata (`created_at`, `version`) and edge carriers
    (`aboutSubject`, `contains_session`, `ownedBy`). `_persist_extra_props` drops
    them, so comparing them would report a missing graph property on every such
    write. (1) Fails if they are compared. (2) Reachable: the producer's own
    payload shape.
    """
    log_path = str(tmp_path / "meta.jsonl")
    events = []
    for k, v in [("created_at", "2026-01-01T00:00:00Z"),
                 ("aboutSubject", "s1"),
                 ("contains_session", "cs1"),
                 ("ownedBy", "o1")]:
        events.append(_pt(f"m_{k}", "a", **{k: v}))
    _seed(proj, log_path, events)
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]


def test_a_verbatim_vector_is_compared_raw(proj, tmp_path):
    """A caller-owned vector is stored RAW (`embedding_verbatim`), everything
    else narrowed to `vecf32`. Comparing both at f32 width would hide a verbatim
    vector that was narrowed — so the storage FORM the flag declares must be
    honoured. (1) Fails if the flag is ignored. (2) Reachable: a 0.1 component,
    which is not exactly representable in float32.
    """
    width = proj.required_embedding_dim
    if not width:
        pytest.skip("store reports no embedding width")
    log_path = str(tmp_path / "verbatim.jsonl")
    vec = [0.1] * width
    _seed(proj, log_path, [_pt("v1", "a", embedding=vec, embedding_verbatim=True)])
    assert check_consistency(log_path, proj)["ok"] is True

    proj.g.query("MATCH (n:Point {id:'v1'}) SET n.embedding=vecf32($v)",
                 {"v": vec})
    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    assert "embedding" in {f for d in r["divergent_points"] for f in d["fields"]}


def test_divergent_points_is_capped_but_counted(proj, tmp_path):
    """A wide divergence must not put one dict per id in the result (the failure
    the check exists to report is also its largest output), and the cap must not
    hide HOW MANY ids diverged.
    (1) Fails if the list is unbounded or the count is the capped length.
    (2) Reachable: 120 tampered points.
    """
    n = 120
    log_path = str(tmp_path / "many.jsonl")
    _seed(proj, log_path, [_pt(f"p{i}", f"c{i}") for i in range(n)])
    proj.g.query("MATCH (n:Point) SET n.content='TAMPERED'")
    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    assert len(r["divergent_points"]) <= 50, len(r["divergent_points"])
    assert r["divergent_point_count"] == n, r["divergent_point_count"]


# ── the writer's ORDERING rules for the lifecycle arms ────────────────────


@pytest.mark.parametrize("terminal", [
    {"type": "PointRetracted", "id": "x"},
    {"type": "PointSuperseded", "id": "x", "new_id": "y",
     "valid_to": "2026-02-01T00:00:00Z", "expired_at": "2026-02-01T00:00:00Z"},
    {"type": "PointInvalidated", "id": "x",
     "valid_to": "2026-02-01T00:00:00Z", "expired_at": "2026-02-01T00:00:00Z"},
])
def test_a_belief_write_after_a_terminalizer_wins(proj, tmp_path, terminal):
    """A terminalizer's decay applies AT ITS OWN JOURNAL POSITION (#2884 A3) —
    the writer moved it out of the trailing sweep precisely because a post-hoc
    application clobbered every later belief writer. So a `PointRevised` after a
    retract/supersede/invalidate must WIN on the journal side too.
    (1) Fails if the lifecycle arms are applied after the whole fold: the
    journal side then reads `confidence=0.5` while the graph (and both guarded
    repairs) hold the later revision's value.
    (2) Reachable: the two-event shape `retract_point` + `update_point`.
    """
    events_dir = tmp_path / terminal["type"]
    events_dir.mkdir()
    log_path = str(events_dir / "events.jsonl")
    _seed(proj, log_path, [
        _pt("x", "a", confidence=0.9),
        terminal,
        {"type": "PointRevised", "id": "x", "new_content": "a2",
         "confidence": 0.8},
    ])
    proj.query("MATCH (n) DETACH DELETE n")
    proj.rebuild_all(str(events_dir))

    r = check_consistency(log_path, proj)
    assert r["ok"] is True, (terminal["type"], r["divergent_points"])


def test_a_later_creation_replaces_a_terminalized_incarnation(proj, tmp_path):
    """The ordered pass: a `PointAdded` for an id that a terminalizer already
    tombstoned IS the writer's recreate — the new incarnation is live, and the
    old terminalizer must not be stamped onto it.
    (1) Fails if the lifecycle arms are applied post-hoc over the whole list (the
    entry is then `status='retracted'` with the decayed confidence).
    (2) Reachable by construction: the two-event shape `retract_point` then
    `create_point` on the same id.
    """
    from tortoise.consistency import _fold_journal
    events = [
        _pt("x", "old", confidence=0.9),
        {"type": "PointRetracted", "id": "x"},
        _pt("x", "new", confidence=0.7),
    ]
    entry = _fold_journal(events)["x"]
    assert entry["content"] == "new", entry
    assert entry["status"] == "live", entry
    assert entry["confidence"] == 0.7, entry


def test_a_supersede_without_new_id_is_a_no_op_like_the_writer(proj, tmp_path):
    """`_fold_point_superseded` starts `if not oid or not new_id: return 0`, so a
    PointSuperseded without `new_id` changes nothing on the graph. The journal
    side must not decay the point for it.
    (1) Fails if the arm is applied unconditionally (7 false-positive fields).
    (2) Reachable: a supersede event with no `new_id`.
    """
    events_dir = tmp_path / "noid"
    events_dir.mkdir()
    log_path = str(events_dir / "events.jsonl")
    _seed(proj, log_path, [
        _pt("x", "a", confidence=0.9),
        {"type": "PointSuperseded", "id": "x",
         "valid_to": "2026-02-01T00:00:00Z"},
    ])
    proj.query("MATCH (n) DETACH DELETE n")
    proj.rebuild_all(str(events_dir))
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]


def test_a_wrong_width_graph_vector_is_a_divergence(proj, tmp_path):
    """Both sides carrying a vector of DIFFERENT width is not a near-miss: the
    writer stores the journal's width, so the graph holds a vector the journal
    does not describe. Without this branch the case fell through every arm and
    was adopted green.
    (1) Fails if only the equal-width case is compared.
    (2) Reachable: a 384-wide journal vector against an 8-wide graph one.
    """
    width = proj.required_embedding_dim
    if not width:
        pytest.skip("store reports no embedding width")
    log_path = str(tmp_path / "width.jsonl")
    _seed(proj, log_path, [_pt("v1", "a", embedding=[0.25] * width)])
    assert check_consistency(log_path, proj)["ok"] is True

    proj.g.query("MATCH (n:Point {id:'v1'}) SET n.embedding=vecf32($v)",
                 {"v": [0.25] * 8})
    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    assert "embedding" in {f for d in r["divergent_points"] for f in d["fields"]}


def test_a_narrowed_verbatim_vector_is_caught_by_the_journal_flag(proj, tmp_path):
    """The storage FORM comes from the journal (`embedding_verbatim` rides the
    payload): a graph that narrows a caller-owned vector AND drops its own
    `embedding_verbatim` flag must still be caught, so the flag cannot be read
    from the side being judged.
    (1) Fails if the flag is read from the graph (the narrowing then compares
    f32-vs-f32 and reads equal). (2) Reachable: a 0.1 component + a nulled flag.
    """
    width = proj.required_embedding_dim
    if not width:
        pytest.skip("store reports no embedding width")
    log_path = str(tmp_path / "narrow.jsonl")
    vec = [0.1] * width
    _seed(proj, log_path, [_pt("v1", "a", embedding=vec, embedding_verbatim=True)])
    assert check_consistency(log_path, proj)["ok"] is True

    proj.g.query("MATCH (n:Point {id:'v1'}) "
                 "SET n.embedding=vecf32($v), n.embedding_verbatim=null",
                 {"v": vec})
    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    assert "embedding" in {f for d in r["divergent_points"] for f in d["fields"]}


def test_a_promotion_snapshot_without_the_operator_key_resets_operator_ness(
        proj, tmp_path):
    """`_upsert_point_props` sets `n.is_operator=$isop`/`n.op_type=$opt`
    UNCONDITIONALLY from the NESTED `operator` key, so a promotion snapshot that
    omits it converts the node to a claim. `content`/`is_operator`/`op_type` are
    the three non-`coalesce` SETs the merge must therefore PIN, not preserve.
    (1) Fails if the merge preserves the prior nested operator.
    (2) Reachable: an OperatorPromoted snapshot without `operator`.
    """
    log_path = str(tmp_path / "noop.jsonl")
    events = [
        {**_pt("op1", "IMPL(a)"), "point": dict(_pt("op1", "IMPL(a)")["point"],
                                                operator={"op_type": "IMPL"})},
        {"type": "OperatorPromoted",
         "point": {"id": "op1", "status": "live",
                   "createdAt": "2026-01-01T00:00:00Z"}},
    ]
    _seed(proj, log_path, events)
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]


def test_a_repair_that_drops_an_uncarried_key_is_not_an_unrecorded_mutation(
        proj, tmp_path):
    """A DECLARED live/replay asymmetry must not move the baseline: the live
    writer stores `tags`, the replayed writer drops it (#2897), so a repair over
    the same journal leaves a graph the baseline no longer matches. Excluding the
    per-point `uncarried` keys from the fingerprint is what keeps a repair from
    reading as an unrecorded mutation for ever.
    (1) Fails if the fingerprint includes comparison-skipped keys.
    (2) Reachable: `tags` on a live write, then `rebuild_all`.
    """
    events_dir = tmp_path / "tags"
    events_dir.mkdir()
    log_path = str(events_dir / "events.jsonl")
    _seed(proj, log_path, [_pt("p1", "a", tags=["alpha", "beta"])])
    first = check_consistency(log_path, proj)
    assert first["ok"] is True and first["state_recorded"] is True
    assert "tags" in first["uncarried_journal_fields"]

    proj.query("MATCH (n) DETACH DELETE n")
    proj.rebuild_all(str(events_dir))
    r = check_consistency(log_path, proj)
    assert r["ok"] is True, r["divergent_points"]
    assert r["divergence"] is None


def test_the_present_path_is_also_capped(proj, tmp_path):
    """The cap covers the `__present__` path too (a mass deletion/addition), not
    only the field path — and `divergent_point_count` stays exact.
    (1) Fails if the ghost path appends one dict per id. (2) Reachable: 60 ghost
    nodes against a 1-point journal.
    """
    log_path = str(tmp_path / "ghosts.jsonl")
    _seed(proj, log_path, [_pt("real", "a")])
    for i in range(60):
        proj.g.query("CREATE (:Point {id:$id, content:'ghost'})", {"id": f"g{i}"})
    r = check_consistency(log_path, proj)
    assert r["ok"] is False
    assert len(r["divergent_points"]) <= 50
    # 60 ghosts; `real` is present on both sides, so it diverges on the COUNT
    # (`delta`) rather than per-point.
    assert r["divergent_point_count"] == 60, r["divergent_point_count"]
    assert r["delta"] == 1 - 61
