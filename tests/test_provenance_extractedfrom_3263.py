"""#3263 — Point provenance edge is written on every write path that HAS a
source, and survives rebuild (live == rebuild).

Bug: ``(Point)-[:extractedFrom]->(:Source)`` was never written for
session-context writes. The eval ingest paths (``ingest.py`` /
``ingest_v2.py``) pass ``session_id`` but no ``extractedFrom``, so every
extracted claim landed as an orphan Point with no Source — making source-tier
calibration (``credibilityTier`` → inherited prior) unsatisfiable BY
CONSTRUCTION (#3263; the downstream no-op is #3139).

Fix: ``create_point`` INFERS the provenance ref from the write context —
``session:<session_id>``, the SAME ref the capture path wires explicitly
(#1350) — when the caller does not supply one. The inferred ref rides the
SAME ``extractedFrom`` prop path as an explicit one, so it is journaled on
PointAdded and replayed by ``_upsert_point_edges`` (live == rebuild).

DB-lane tests (live FalkorDB under TORTOISE_DB_URI; also embedded-safe — no
FTS/vector leg needed).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def prov(tmp_path):
    """(sdk, events_dir) with the JSONL journal wired for rebuild."""
    db = os.path.join(str(tmp_path), "prov.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _provenance(proj) -> set[tuple[str, str, str]]:
    """The full live provenance surface: (point id, source url, sourceKind)."""
    return {
        (r[0], r[1], r[2])
        for r in proj.g.query(
            "MATCH (p:Point)-[:extractedFrom]->(s:Source) "
            "RETURN p.id, s.url, s.sourceKind").result_set
    }


def _sources_of(proj, pid: str) -> set[str]:
    return {
        r[0]
        for r in proj.g.query(
            "MATCH (p:Point {id:$id})-[:extractedFrom]->(s:Source) "
            "RETURN s.url",
            params={"id": pid}).result_set
    }


# ── control: the explicit path (unchanged) ────────────────────────────────


def test_explicit_extractedfrom_still_wires_the_edge(prov):
    sdk, _events = prov
    p = sdk.create_point("statement", "explicit source claim",
                         extractedFrom="https://doc.example/a")
    assert _sources_of(sdk._get_proj(), p["id"]) == {"https://doc.example/a"}


# ── the fix: session context INFERS the source ────────────────────────────


def test_session_context_wires_extractedfrom(prov):
    """A write with a session context has a derivable Source — it must be
    wired, not left orphaned."""
    sdk, _events = prov
    p = sdk.create_point("statement", "session-context claim",
                         session_id="sess-abc")
    assert _sources_of(sdk._get_proj(), p["id"]) == {"session:sess-abc"}


def test_eval_ingest_shape_wires_extractedfrom(prov):
    """The exact call shape the eval ingest paths use (status=draft,
    is_episodic, session_id, NO extractedFrom) — the reported failing
    path."""
    sdk, _events = prov
    sid = "lme:q42:s0"
    p = sdk.create_point("statement", "eval ingested claim",
                         session_id=sid, lme_question_id="q42",
                         lme_session_index=0, is_episodic=True,
                         status="draft")
    assert _sources_of(sdk._get_proj(), p["id"]) == {f"session:{sid}"}


def test_real_ingest_v2_write_payload_wires_extractedfrom(prov):
    """End-to-end through the REAL eval writer (``_write_payload``) — the
    module the issue measured as containing no ``extractedFrom`` /
    ``:Source`` reference at all."""
    from tools.longmem_eval.ingest_v2 import _write_payload

    sdk, _events = prov
    sid = "lme:q7:s1"
    turns = [{"role": "user", "content": "I bought the grey couch in March."}]
    payload = {
        "points": [{"id": "pt_eval_1",
                    "content": "bought the grey couch in March",
                    "about_entities": []}],
        "entities": [], "events": [], "operators": [],
    }
    _write_payload(sdk, payload, sid=sid, qid="q7", si=1,
                   evidence_turns=[], turns=turns, ev_sessions=set(),
                   n_turns=1)
    assert _sources_of(sdk._get_proj(), "pt_eval_1") == {f"session:{sid}"}


def test_explicit_extractedfrom_wins_over_session_inference(prov):
    sdk, _events = prov
    p = sdk.create_point("statement", "both contexts",
                         session_id="sess-abc",
                         extractedFrom="https://doc.example/explicit")
    assert _sources_of(sdk._get_proj(), p["id"]) == {
        "https://doc.example/explicit"}


def test_write_without_source_context_stays_unlinked(prov):
    """No session, no explicit ref → nothing derivable → no edge (and no
    invented Source)."""
    sdk, _events = prov
    p = sdk.create_point("statement", "bare agent thought")
    assert _sources_of(sdk._get_proj(), p["id"]) == set()
    assert _provenance(sdk._get_proj()) == set()


# ── live == rebuild ───────────────────────────────────────────────────────


def test_live_and_rebuild_agree_on_inferred_extractedfrom(prov):
    """The inferred edge must survive the journal → wipe → replay cycle
    byte-for-byte (same url AND same sourceKind)."""
    sdk, events = prov
    sid = "lme:q99:s2"
    sdk.create_point("statement", "claim one", session_id=sid,
                     is_episodic=True, status="draft")
    sdk.create_point("statement", "claim two", session_id="sess-zzz",
                     is_episodic=True, status="draft")
    sdk.create_point("statement", "explicit claim",
                     extractedFrom="https://doc.example/keep")
    live = _provenance(sdk._get_proj())
    assert live, "guard: the live graph must have provenance to rebuild"

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _provenance(sdk._get_proj()) == live, \
        "extractedFrom diverged between live write and rebuild"


def test_rebuild_preserves_session_source_is_episodic(prov):
    """A ``session:`` ref mints an episodic Source on BOTH paths (the
    ``_mint_source_stub`` shared create path, #1486) — live == rebuild."""
    sdk, events = prov
    sdk.create_point("statement", "episodic claim", session_id="sess-ep",
                     is_episodic=True, status="draft")

    def _is_episodic(proj) -> bool:
        return bool(proj.g.query(
            "MATCH (s:Source {url:'session:sess-ep'}) "
            "RETURN coalesce(s.is_episodic, false)").result_set[0][0])

    assert _is_episodic(sdk._get_proj()) is True, "live Source not episodic"
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _is_episodic(sdk._get_proj()) is True, \
        "rebuilt Source lost is_episodic"


# ── #7704 OWNER RULING (2026-10-25): a dedup fold KEEPS the evidence ───────
#
# The owner ruled the W4-b "twin" case a **duplication defect**, not a ranking
# one: *"The dedup is the key here to solve the problem you had raised"*, and
# *"one claim carrying N sources is the intended state"*. Until this change the
# fold had no way to add provenance to a Point that already existed (the
# journaled path the old warning said was "tracked separately (#3292)" — and
# #3292 closed on its first-write INFERENCE half only, leaving the residual
# unowned), so a re-write that carried a NEW source dropped it and said so.
#
# These pins are the RE-DERIVATION of that warn-and-drop trio: same intent
# (a ref supplied to a dedup hit is never silently lost), now asserted on the
# fact instead of on the warning that existed because the fact was impossible.


def _prop_refs(proj, pid: str):
    """The Point's own ``extractedFrom`` node property (the reported surface)."""
    got = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.extractedFrom",
        params={"id": pid}).result_set
    return got[0][0] if got else None


def _seed_hashed_source(sdk, url: str, content_hash: str) -> None:
    """A ``:Source`` carrying a RECORDED read-version (the document/ingest shape).

    ``sourceVersionTransit`` (#5256) carries a pair only for a Source whose
    ``contentHash`` is non-blank, so a pin built on bare stubs never constructs
    the carrier at all: the fold pops the key and the property the live write is
    supposed to carry does not exist to be missed (#7704 review P1).

    Seeded through the PRODUCT door (``create_source``, journaled and replayed),
    not a raw write: §17.1's handle is armed for a journal-backed SDK, so a raw
    fixture statement would need a lane and would not survive the rebuilds these
    pins run.
    """
    sdk.create_source(url, "document", contentHash=content_hash)


def test_dedup_hit_accumulates_the_second_source(prov):
    """#7704: two sources, one claim — the fold keeps BOTH.

    Two writes of the SAME claim (byte-identical content, same kind) are ONE
    Point (that is what dedup identity buys). The evidence difference between
    them is the point: the second write's source must join the first's, not
    replace it and not disappear.

    Frozen before: Point=1, Sources=1, ``extractedFrom='https://…/alpha'`` —
    the beta source was never even minted, and a rebuild reproduced the loss
    because the fold journalled nothing.
    """
    sdk, events = prov
    content = "quarterly revenue target raised to 40 million"
    first = sdk.create_point("statement", content,
                             extractedFrom="https://example.com/alpha",
                             dedup=True)
    second = sdk.create_point("statement", content,
                              extractedFrom="https://example.com/beta",
                              dedup=True)
    assert second["id"] == first["id"], (
        "guard: identical content + kind must be ONE claim (a dedup hit)")

    proj = sdk._get_proj()
    pid = first["id"]
    assert sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0] == 1, \
        "a duplicated claim must appear ONCE"
    assert _sources_of(proj, pid) == {
        "https://example.com/alpha", "https://example.com/beta"}, \
        "the fold dropped the second source — the evidence difference is LOST"
    # The prop is the surface `list_drafts` reports; it must agree with the
    # edges (a SEQUENCE is now the accumulated list, never a scalar).
    assert sorted(_prop_refs(proj, pid)) == [
        "https://example.com/alpha", "https://example.com/beta"]

    live = _provenance(proj)
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _provenance(proj) == live, \
        "the accumulated provenance did not survive a rebuild (live != rebuild)"
    assert sorted(_prop_refs(proj, pid)) == [
        "https://example.com/alpha", "https://example.com/beta"]


def test_dedup_hit_accumulates_the_inferred_session_source(prov):
    """The same fold for the INFERRED ref (``session:<session_id>``).

    This is the shape the capture/ingest lanes produce: they pass a session
    context and no explicit ref, so the second session to restate the claim is
    its second piece of evidence.
    """
    sdk, events = prov
    content = "the enterprise pilot closes in the third quarter"
    first = sdk.create_point("statement", content,
                             session_id="sess-one", dedup=True)
    second = sdk.create_point("statement", content,
                              session_id="sess-two", dedup=True)
    assert second["id"] == first["id"], "guard: this must be a dedup hit"

    proj = sdk._get_proj()
    assert _sources_of(proj, first["id"]) == {
        "session:sess-one", "session:sess-two"}, \
        "the fold dropped the second session's provenance"

    live = _provenance(proj)
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _provenance(proj) == live, "accumulated session refs did not replay"


def test_the_fold_journals_a_state_the_checker_agrees_with(prov):
    """The re-emitted record must describe the graph the fold actually left.

    A fold that stops at the cache puts the derived store ahead of truth: a
    replay silently loses the evidence and no repair can recover it. The mirror
    image is just as bad — a record that does not match the node it claims to
    describe — and `check_consistency` is the repository's own instrument for
    exactly that pair (journal fold vs graph). Asserting it here holds the new
    record to the same bar as every other write, instead of to "a rebuild
    produces some edges".

    ``check_consistency`` ADOPTS a pre-existing graph on its first run, so the
    baseline is taken after the create and the verdict is read after the fold.

    Both Sources carry a RECORDED ``contentHash``: the carrier branch is only
    constructed for a Source that has one (a stub contributes no pair), and a
    pin built on stubs passed while the fold journalled a ``sourceVersionTransit``
    the live node never received (#7704 review P1).
    """
    from tortoise.consistency import check_consistency

    sdk, events = prov
    log_path = os.path.join(str(events), "events.jsonl")
    proj = sdk._get_proj()
    first_ref, second_ref = ("https://example.com/first",
                             "https://example.com/second")
    _seed_hashed_source(sdk, first_ref, "hash-first")
    _seed_hashed_source(sdk, second_ref, "hash-second")
    content = "the expansion is funded through next spring"
    first = sdk.create_point("statement", content,
                             extractedFrom=first_ref,
                             dedup=True)
    check_consistency(log_path, proj)          # adopt the existing graph

    sdk.create_point("statement", content,
                     extractedFrom=second_ref, dedup=True)

    result = check_consistency(log_path, proj)
    assert result["ok"], (
        "the fold's journal record does not describe the graph it wrote — a "
        f"replay would diverge from live: {result.get('divergent_points')}")
    assert result["hash_match"], result
    assert not result.get("divergent_points"), result["divergent_points"]
    assert _sources_of(proj, first["id"]) == {first_ref, second_ref}


def test_the_fold_replays_field_by_field(prov):
    """``derived = replay(journal)`` for the fold — field by field.

    The decisive comparison, and the one a "a rebuild produced some edges" pin
    cannot make: both a LOSSY fold and a DIVERGENT one satisfy the edge count.
    The replayed row must equal the live row on every field the fold wrote — the
    ``extractedFrom`` property, the ``sourceVersionTransit`` carrier (#5256), and
    each edge's recorded read-version.

    Frozen before (#7704 review P1): the fold journalled the merged carrier but
    the live write only set ``extractedFrom``, so the replay re-stamped
    ``[[one, hash-one], [two, hash-two]]`` on a node live held as
    ``[[one, hash-one]]`` — the provenance the change exists to keep was kept in
    the journal ALONE.
    """
    sdk, events = prov
    proj = sdk._get_proj()
    one, two = "https://example.com/one", "https://example.com/two"
    _seed_hashed_source(sdk, one, "hash-one")
    _seed_hashed_source(sdk, two, "hash-two")

    content = "the fold must be replayable field by field"
    pid = sdk.create_point("statement", content, extractedFrom=one,
                           dedup=True)["id"]
    sdk.create_point("statement", content, extractedFrom=two, dedup=True)
    assert _sources_of(proj, pid) == {one, two}, \
        "guard: the second write must be a fold, not a second Point"

    def _row(projection) -> dict:
        props = projection.g.query(
            "MATCH (p:Point {id:$id}) RETURN p.extractedFrom, "
            "p.sourceVersionTransit", params={"id": pid}).result_set[0]
        return {
            "extractedFrom": props[0],
            "sourceVersionTransit": props[1],
            "edges(url, sourceVersion)": sorted(
                (r[0], r[1]) for r in projection.g.query(
                    "MATCH (p:Point {id:$id})-[r:extractedFrom]->(s:Source) "
                    "RETURN s.url, r.sourceVersion",
                    params={"id": pid}).result_set),
        }

    live = _row(proj)
    assert live["sourceVersionTransit"] == [[one, "hash-one"],
                                            [two, "hash-two"]], live
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _row(proj) == live, (
        "the journal does not reproduce the row live wrote — replay(journal) "
        f"!= live: live={live!r} replayed={_row(proj)!r}")


def test_a_url_variant_of_a_held_ref_is_not_refolded(prov):
    """A held ref supplied under a URL VARIANT is PRESENT — no fold, no record.

    ``_present`` is the edges' RESOLVED ``s.url`` set, while the caller supplies
    a RAW spelling; ``resolve_source_key`` (S0b, #5012) is the one resolver every
    source writer uses to map one onto the other and ``_link_source`` routes
    through it. A comparison that skipped it read a ref the Point already holds
    as permanently absent, so a re-ingested variant (the normal connector shape)
    re-journalled a full ``PointAdded`` on every retry and widened the property
    with a spelling that has no Source and no edge — a prop-only lie a rebuild
    makes permanent instead of healing (#7704 review P1).
    """
    sdk, events = prov
    proj = sdk._get_proj()
    journal = os.path.join(str(events), "events.jsonl")
    held = "https://example.com/a"

    def _point_added() -> int:
        import json
        n = 0
        with open(journal) as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if (rec.get("type") == "PointAdded"
                        and (rec.get("point") or {}).get("id") == pid):
                    n += 1
        return n

    content = "a url variant is the same source"
    pid = sdk.create_point("statement", content, extractedFrom=held,
                           dedup=True)["id"]
    before = _point_added()
    for _ in range(3):
        assert sdk.create_point(
            "statement", content,
            extractedFrom=held + "?utm_source=x", dedup=True)["id"] == pid
    assert _point_added() == before, (
        "an idempotent retry journalled a full snapshot — the fold is not "
        f"idempotent ({before} -> {_point_added()})")
    assert _sources_of(proj, pid) == {held}
    assert _prop_refs(proj, pid) == held, (
        "the property states a spelling the graph has no Source for — a "
        f"prop-only lie: {_prop_refs(proj, pid)!r}")
    assert proj.g.query(
        "MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1, \
        "a URL variant minted a second :Source"


def test_a_fold_whose_record_cannot_be_written_refuses_the_fold(prov,
                                                               monkeypatch):
    """§17.1 — NO record ⇒ NO fold.

    The fold's ``PointAdded`` append is its commit point. If it fails, the edges
    must not be wired and the property must not move: a cache written without a
    record is AHEAD of truth, where a replay silently loses the evidence and no
    repair can recover it — the exact loss the fold exists to stop.

    MUTATION (the review's arm — ``if _committed is False:`` → ``if False:``):
    the fold then lands with no record, and this pin fails because
    ``WriteNotLandedError`` is never raised.
    """
    from tortoise.exceptions import WriteNotLandedError

    sdk, _events = prov
    proj = sdk._get_proj()
    content = "a claim whose fold cannot be recorded"
    held, incoming = "https://example.com/held", "https://example.com/incoming"
    pid = sdk.create_point("statement", content, extractedFrom=held,
                           dedup=True)["id"]

    log = sdk._get_event_log()
    assert log is not None, "the fixture must wire a journal for this arm"

    def _boom(_event):
        raise OSError("simulated journal append failure")

    monkeypatch.setattr(log, "append", _boom)
    with pytest.raises(WriteNotLandedError) as ei:
        sdk.create_point("statement", content, extractedFrom=incoming,
                         dedup=True)
    assert ei.value.record_committed is False, (
        "the append FAILED, so the record is not committed and the fold must "
        "not be applied — re-issuing is the correct caller response")

    assert _sources_of(proj, pid) == {held}, (
        "the fold wired an edge although its record was never committed — the "
        "derived store leads the truth (§17.1)")
    assert _prop_refs(proj, pid) == held, (
        "the fold moved the property although its record was never committed")
    assert proj.g.query(
        "MATCH (s:Source {url:$u}) RETURN s.url",
        params={"u": incoming}).result_set == [], \
        "a refused fold minted the incoming Source"


def test_repeated_dedup_hit_neither_duplicates_nor_rejournals(prov):
    """The fold is IDEMPOTENT: a re-write of evidence already held writes
    nothing — no duplicate edge, no re-mint of the Source, and no journal
    record (a re-journal on every idempotent retry would grow the log without
    bound and make the replay non-idempotent at the log level).
    """
    sdk, events = prov
    content = "idempotent source claim"
    ref = "https://example.com/once"
    first = sdk.create_point("statement", content, extractedFrom=ref,
                             dedup=True)
    journal = os.path.join(str(events), "events.jsonl")

    def _point_added_for(pid: str) -> int:
        import json
        n = 0
        with open(journal) as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if (rec.get("type") == "PointAdded"
                        and (rec.get("point") or {}).get("id") == pid):
                    n += 1
        return n

    before = _point_added_for(first["id"])
    for _ in range(3):
        again = sdk.create_point("statement", content, extractedFrom=ref,
                                 dedup=True)
        assert again["id"] == first["id"]
    assert _point_added_for(first["id"]) == before, (
        "a no-op dedup hit journalled a record — the fold is not idempotent")
    assert _sources_of(sdk._get_proj(), first["id"]) == {ref}


def test_dedup_hit_keeps_prop_and_edges_truthful(prov):
    """Re-derived from ``test_dedup_hit_does_not_leave_prop_only_provenance``.

    The old test asserted the dedup hit left NO provenance, because forwarding
    the prop into ``update_point`` wrote a prop with no ``:Source`` edge — a
    reported provenance that does not exist, which ``list_drafts`` would show.
    The intent was "never a prop-only lie"; the assertion was the frozen
    consequence (drop it entirely). Now the ref is WIRED, so the intent is
    asserted directly and more strongly: prop and edges agree, on both the
    live graph and after a rebuild.
    """
    sdk, events = prov
    content = "identical claim text"
    first = sdk.create_point("statement", content, dedup=True)
    second = sdk.create_point("statement", content,
                              session_id="sess-dedup", dedup=True)
    assert second["id"] == first["id"], "guard: this must be a dedup hit"

    proj = sdk._get_proj()
    pid = first["id"]
    assert _sources_of(proj, pid) == {"session:sess-dedup"}
    assert _prop_refs(proj, pid) == ["session:sess-dedup"], (
        "the property must state exactly what the edges wire — no prop-only "
        "lie in either direction")

    live = _provenance(proj)
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _provenance(proj) == live


# ── review P2: the session Source kind is agentSession, not document ──────


def test_session_source_kind_is_agentsession(prov):
    """Ontology §4.6 + #909 §4.3 #6 register **agentSession** as the source-kind
    value for session Sources. The old hard-coded ``document`` default
    mislabelled every session Source, put it in ``document``-scoped queries,
    and was invisible to ``audit.missing_sourceKind`` (which passes).
    """
    sdk, events = prov
    p = sdk.create_point("statement", "kind claim", session_id="sess-kind",
                         is_episodic=True, status="draft")

    def _kinds(proj) -> set[str]:
        return {
            r[0]
            for r in proj.g.query(
                "MATCH (p:Point {id:$id})-[:extractedFrom]->(s:Source) "
                "RETURN s.sourceKind",
                params={"id": p["id"]}).result_set
        }

    assert _kinds(sdk._get_proj()) == {"agentSession"}
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _kinds(sdk._get_proj()) == {"agentSession"}, \
        "replay minted the session Source with a different sourceKind"


def test_non_session_source_kind_still_defaults_to_document(prov):
    """Resolving the session default must not re-label ordinary sources."""
    sdk, _events = prov
    p = sdk.create_point("statement", "doc kind claim",
                         extractedFrom="https://doc.example/kind")
    kinds = {
        r[0]
        for r in sdk._get_proj().g.query(
            "MATCH (p:Point {id:$id})-[:extractedFrom]->(s:Source) "
            "RETURN s.sourceKind",
            params={"id": p["id"]}).result_set
    }
    assert kinds == {"document"}


# ── review P2: many-to-many, and never invent a Source ────────────────────


def test_multiple_source_refs_get_one_edge_each(prov):
    """Ontology §3.3 amended to many→many: a claim extracted from several
    sources carries one edge per source. A list used to be accepted silently
    and mint a SINGLE Source whose ``url`` was an ARRAY — a corrupt
    provenance node, with no error raised.
    """
    sdk, _events = prov
    refs = ["https://a.example", "https://b.example", "https://c.example"]
    p = sdk.create_point("statement", "multi-source claim", extractedFrom=refs)
    assert _sources_of(sdk._get_proj(), p["id"]) == set(refs)

    corrupt = sdk._get_proj().g.query(
        "MATCH (s:Source) WHERE s.url STARTS WITH '[' RETURN s.url").result_set
    assert corrupt == [], f"array-valued Source url minted: {corrupt}"


def test_multi_source_survives_rebuild(prov):
    """The fan-out must be reproducible from the journal (live == rebuild)."""
    sdk, events = prov
    refs = ["https://a.example", "https://b.example"]
    p = sdk.create_point("statement", "multi-source rebuild",
                         extractedFrom=refs)
    live = _sources_of(sdk._get_proj(), p["id"])
    assert live == set(refs), "guard: the live fan-out must exist to rebuild"
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _sources_of(sdk._get_proj(), p["id"]) == set(refs)


def test_non_scalar_session_id_does_not_invent_a_source(prov):
    """Design contract #3 — never fabricate provenance. A non-scalar
    ``session_id`` used to stringify into ``session:['s1', 's2']`` and mint
    that bogus Source."""
    sdk, _events = prov
    p = sdk.create_point("statement", "list session id",
                         session_id=["s1", "s2"])
    assert _sources_of(sdk._get_proj(), p["id"]) == set()
    invented = sdk._get_proj().g.query(
        "MATCH (s:Source) WHERE s.url CONTAINS \"['\" RETURN s.url").result_set
    assert invented == [], f"invented Source minted: {invented}"


# ── re-review P1: the bundle write path must resolve LIST refs too ────────


def test_bundle_extractedfrom_list_of_local_refs_links_the_real_sources(prov):
    """The bundle write path resolved only a SCALAR ``extractedFrom`` local ref.

    A LIST of local refs was therefore left unresolved and reached
    ``_link_source`` as the raw strings, so Sources were minted named after the
    LOCAL refs (``s1``, ``s2``) while the real bundle Sources went unlinked —
    inventing exactly the provenance this fix forbids. ``canonical`` already
    resolves list refs element-wise; this mirrors it.
    """
    sdk, _events = prov
    bundle = {
        "points": [
            {"ref": "p1", "kind": "claim",
             "content": "bought the couch in March",
             "extractedFrom": ["s1", "s2"]},
        ],
        "sources": [
            {"ref": "s1", "url": "https://real.example/one",
             "sourceKind": "report"},
            {"ref": "s2", "url": "https://real.example/two",
             "sourceKind": "report"},
        ],
        "connections": [],
    }
    sdk.ingest(bundle)
    proj = sdk._get_proj()

    urls = {url for (_pid, url, _kind) in _provenance(proj)}
    assert urls == {"https://real.example/one", "https://real.example/two"}, \
        f"bundle list refs did not resolve to the real Sources: {urls}"

    invented = proj.g.query(
        "MATCH (s:Source) WHERE s.url IN ['s1', 's2'] RETURN s.url").result_set
    assert invented == [], f"invented Sources minted from local refs: {invented}"


# ── cycle-3: bundle validation, connection fan-out, warning gating ────────


def test_bundle_rejects_non_string_extractedfrom_element(prov):
    """Phase 1 must reject a malformed ``extractedFrom`` while rejection is
    still free. A non-string element either raised a raw ResponseError from
    Phase 2 AFTER earlier sections had committed (a partial write, violating
    the zero-mutation invariant) or silently minted a Source whose url was an
    array.
    """
    sdk, _events = prov
    for bad in ([['s1']], ["s1", None], "   ", [""]):
        bundle = {
            "points": [{"ref": "p1", "kind": "claim", "content": "claim",
                        "extractedFrom": bad}],
            "sources": [{"ref": "s1", "url": "https://real.example/one",
                         "sourceKind": "report"}],
            "connections": [],
        }
        with pytest.raises(Exception, match="extractedFrom"):
            sdk.ingest(bundle)
    # And nothing was committed by the rejected bundles.
    assert _provenance(sdk._get_proj()) == set()


def test_bundle_empty_extractedfrom_list_is_allowed(prov):
    """An empty list is explicit "no provenance" — equivalent to absent, not
    an error (guards against the validation over-reaching)."""
    sdk, _events = prov
    bundle = {
        "points": [{"ref": "p1", "kind": "claim", "content": "no prov",
                    "extractedFrom": []}],
        "sources": [], "connections": [],
    }
    sdk.ingest(bundle)
    assert _provenance(sdk._get_proj()) == set()


def test_bundle_connection_extractedfrom_fans_out_to_every_target(prov):
    """The connection surface is the second documented way to express
    provenance refs. With a multi-target ``to`` it linked only ``dsts[0]`` —
    silently dropping the rest, with no error and no counter signal — on the
    very surface this change set amended to many→many.
    """
    sdk, _events = prov
    bundle = {
        "points": [{"ref": "p1", "kind": "claim", "content": "couch claim"}],
        "sources": [
            {"ref": "s1", "url": "https://real.example/one",
             "sourceKind": "report"},
            {"ref": "s2", "url": "https://real.example/two",
             "sourceKind": "report"},
        ],
        "connections": [
            {"ref": "c1", "from": "p1", "to": ["s1", "s2"],
             "relation": "extractedFrom"},
        ],
    }
    res = sdk.ingest(bundle)
    urls = {url for (_pid, url, _kind) in _provenance(sdk._get_proj())}
    assert urls == {"https://real.example/one", "https://real.example/two"}, \
        f"multi-target extractedFrom connection lost a source: {urls}"
    assert res["created"]["connections"] == 2


def test_reingest_does_not_warn_about_provenance(prov, caplog):
    """Re-ingest is advertised as safe/MERGE-based. The dedup skip used to
    WARN unconditionally — one false warning per Point per re-ingest (the eval
    lane re-ingests corpora), asserting the opposite of the graph state, since
    the Point already carries the edge.
    """
    sdk, _events = prov
    bundle = {
        "points": [{"ref": "p1", "kind": "claim", "content": "idempotent claim",
                    "extractedFrom": "session:sess-re"}],
        "sources": [], "connections": [],
    }
    sdk.ingest(bundle)
    with caplog.at_level(logging.WARNING):
        sdk.ingest(bundle)
    noise = [r.getMessage() for r in caplog.records
             if "not applied" in r.getMessage()]
    assert noise == [], f"spurious provenance warning on re-ingest: {noise}"


def test_reingest_with_empty_ref_list_does_not_warn(prov, caplog):
    """An explicit ``[]`` means "no provenance" (the bundle contract certifies
    it). The first gate keyed on ``is not None``, so it still warned on every
    idempotent re-ingest — the caller supplied NO ref, yet was told to "pass
    the ref on the FIRST write"."""
    sdk, _events = prov
    bundle = {
        "points": [{"ref": "p1", "kind": "claim", "content": "no prov re",
                    "extractedFrom": []}],
        "sources": [], "connections": [],
    }
    sdk.ingest(bundle)
    with caplog.at_level(logging.WARNING):
        sdk.ingest(bundle)
    noise = [r.getMessage() for r in caplog.records
             if "not applied" in r.getMessage()]
    assert noise == [], f"spurious warning for an explicit empty ref list: {noise}"


def test_dedup_hit_with_a_CHANGED_ref_accumulates_it(prov, caplog):
    """Re-derived from ``test_dedup_hit_with_a_CHANGED_ref_warns``.

    A DIFFERENT ref on a dedup hit used to be treated as "some edge exists" and
    dropped with a warning: the caller believes the Point was relinked when it
    was not. The intent was "a changed ref must not be dropped silently"; the
    assertion was the warning that stood in for the missing journaled path.
    The ref is now folded in, so the intent is asserted on the graph — and the
    absence of the old warning is asserted too, because a ref that IS applied
    must not be reported as not-applied.
    """
    sdk, _events = prov
    content = "changed ref claim"
    first = sdk.create_point("statement", content,
                             extractedFrom="session:sess-OLD", dedup=True)
    with caplog.at_level(logging.WARNING):
        second = sdk.create_point("statement", content,
                                  extractedFrom="session:sess-NEW", dedup=True)
    assert second["id"] == first["id"], "guard: this must be a dedup hit"
    assert _sources_of(sdk._get_proj(), first["id"]) == {
        "session:sess-OLD", "session:sess-NEW"}, \
        "a changed ref on a dedup hit must not be dropped"
    noise = [r.getMessage() for r in caplog.records
             if "not applied" in r.getMessage()]
    assert noise == [], (
        f"the ref WAS applied, so reporting it as dropped is a false warning: "
        f"{noise}")


def test_create_point_rejects_non_string_extractedfrom_prewrite(prov):
    """A non-string scalar reached the fan-out's ``list(source_ref)``, raising
    TypeError AFTER the Point was CREATEd but BEFORE PointAdded was journaled —
    leaving an orphan that a rebuild would drop (live != rebuild). It must fail
    closed BEFORE the write.
    """
    sdk, _events = prov
    before = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0]
    for bad in (123, 1.5, ["ok", None], [None]):
        with pytest.raises(ValueError, match="extractedFrom"):
            sdk.create_point("statement", f"bad ref {bad!r}",
                             extractedFrom=bad)
    after = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0]
    assert after == before, "a rejected Point was still written (orphan)"


def test_create_point_treats_blank_ref_as_absent(prov):
    """An empty/blank string is explicit "none", not an error — so session
    inference still applies and nothing is invented."""
    sdk, _events = prov
    p = sdk.create_point("statement", "blank ref claim",
                         extractedFrom="   ", session_id="sess-blank")
    assert _sources_of(sdk._get_proj(), p["id"]) == {"session:sess-blank"}


def test_dedup_hit_without_the_edge_WIRES_it(prov):
    """Re-derived from ``test_dedup_hit_without_the_edge_DOES_warn``.

    The Point genuinely had no Source edge and the dedup hit supplied one: the
    skip was consequential, so it had to be visible. It is now visible as the
    fact — the edge exists — rather than as a warning about its absence.
    """
    sdk, _events = prov
    content = "warn-me claim"
    sdk.create_point("statement", content, dedup=True)          # no source ctx
    hit = sdk.create_point("statement", content, session_id="sess-w",
                           dedup=True)
    assert _sources_of(sdk._get_proj(), hit["id"]) == {"session:sess-w"}, \
        "a dedup hit that supplies the Point's FIRST source left it an orphan"


def test_falsy_non_string_session_id_warns(prov, caplog):
    """`0`/`False` are neither strings nor absence. Keying the guard on
    truthiness silently skipped them, contradicting the stated rule."""
    sdk, _events = prov
    with caplog.at_level(logging.WARNING):
        sdk.create_point("statement", "zero session id", session_id=0)
    assert any("not a non-empty string" in r.getMessage()
               for r in caplog.records), "falsy non-string session_id was silent"
