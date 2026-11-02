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

import hashlib
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


def _render_row(proj, pid: str) -> dict:
    """The row the fold writes, as the replay comparison must see it.

    ``extractedFrom`` (the convenience property), ``extractedFromAll`` (the
    folded SET, #7704 review P1), ``sourceVersionTransit`` (the carrier,
    #5256) and every edge's recorded read-version — the surfaces
    ``_fold_source_evidence`` touches. Shared so the parity pins cannot drift
    apart from each other; the two ref surfaces are compared SEPARATELY because
    live and replay must agree on each of them independently.
    """
    props = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.extractedFrom, "
        "p.sourceVersionTransit, p.extractedFromAll",
        params={"id": pid}).result_set[0]
    return {
        "extractedFrom": props[0],
        "extractedFromAll": props[2],
        "sourceVersionTransit": props[1],
        "edges(url, sourceVersion)": sorted(
            (r[0], r[1]) for r in proj.g.query(
                "MATCH (p:Point {id:$id})-[r:extractedFrom]->(s:Source) "
                "RETURN s.url, r.sourceVersion",
                params={"id": pid}).result_set),
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
    """The Point's own ``extractedFrom`` node property (the reported surface).

    ⛔ A SCALAR after a fold holds refs (#7704 review P1): the survivor's own
    creating source. ``sdk.query`` / ``paginated_query`` match this property by
    EQUALITY (``n.`extractedFrom` = $extractedFrom``), and an ARRAY is not
    equality-matchable — `test_a_fold_keeps_the_read_door_answering` is the pin
    that the read door still answers. The folded SET is ``_all_prop_refs``.
    """
    got = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.extractedFrom",
        params={"id": pid}).result_set
    return got[0][0] if got else None


def _all_prop_refs(proj, pid: str):
    """The FOLDED ref set on the node (``extractedFromAll``, #7704 review P1).

    The second ref surface the fold writes: the convenience property keeps ONE
    matchable scalar, so the full set a fold produced rides here. The EDGES
    remain authoritative (#3263) — this is a query convenience beside the
    scalar, and the pins assert it against ``_sources_of`` rather than instead
    of it.
    """
    got = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.extractedFromAll",
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
    # The prop is the surface `list_drafts` reports AND the surface
    # `query(extractedFrom=…)` matches by EQUALITY: it stays the scalar the
    # Point was created under, with the folded set beside it (#7704 review P1).
    assert _prop_refs(proj, pid) == "https://example.com/alpha", (
        "the convenience property must stay the survivor's own creating "
        f"source: {_prop_refs(proj, pid)!r}")
    assert sorted(_all_prop_refs(proj, pid)) == [
        "https://example.com/alpha", "https://example.com/beta"]

    live = _provenance(proj)
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _provenance(proj) == live, \
        "the accumulated provenance did not survive a rebuild (live != rebuild)"
    assert _prop_refs(proj, pid) == "https://example.com/alpha"
    assert sorted(_all_prop_refs(proj, pid)) == [
        "https://example.com/alpha", "https://example.com/beta"]


def test_a_fold_keeps_the_read_door_answering(prov):
    """#7704 review P1: the fold must not make its own survivor unfindable.

    ``sdk.query`` / ``paginated_query`` (SDK **and** MCP) filter with
    ``n.`extractedFrom` = $extractedFrom`` — an EQUALITY match — and an ARRAY
    is not equality-matchable. Writing the accumulated LIST into that property
    therefore dropped the survivor out of a PUBLIC read door for every source
    it carries, INCLUDING the one it was created under, and before that fold
    ``origin/main`` answered: a matchable scalar became an unmatchable array.

    The same regression reached ``integrations/cli-extraction/extract_turns.py``:
    ``_source_points`` (the writer's only population read — ``total_for_source``
    and ``new`` under-report) and ``dump``'s
    ``WHERE p.extractedFrom STARTS WITH 'codex://'``, a string operator applied
    to a list.

    ⛔ The fix is NOT a CONTAINS rewrite inside ``query``: the property stays
    the scalar it always was (the survivor's own creating source) and the
    folded set rides ``extractedFromAll``. A survivor created under ANOTHER
    source is still not returned under THIS one's ref — the answer
    ``origin/main`` gave, and the residual #10063 tracks — but nothing that
    answered before the fold stops answering.

    MUTATION: write ``accumulated`` back into the property (the pre-fix arm) and
    the three read-door assertions below red while the edges stay green.
    """
    sdk, _events = prov
    alpha, beta = ("https://example.com/alpha", "https://example.com/beta")
    content = "the fold must not hide the survivor from the read door"
    pid = sdk.create_point("statement", content, extractedFrom=alpha,
                           dedup=True)["id"]
    assert [p["id"] for p in sdk.query(extractedFrom=alpha)] == [pid], \
        "guard: the creating source must answer BEFORE the fold"

    sdk.create_point("statement", content, extractedFrom=beta, dedup=True)

    assert [p["id"] for p in sdk.query(extractedFrom=alpha)] == [pid], (
        "the fold made the survivor unfindable under the source it was "
        "CREATED under — the property is no longer equality-matchable")
    page = sdk.paginated_query(extractedFrom=alpha)
    assert [p["id"] for p in page["results"]] == [pid], (
        "paginated_query went the same way — the whole read door, not one "
        f"method: {sorted(page)!r}")
    assert sdk.query(extractedFrom=beta) == [], (
        "#10063 residual, asserted so it cannot silently change shape: the "
        "property holds ONE creating source, so the OTHER source's ref does "
        "not match it — the EDGES are what carry that fact (#3263)")


def test_a_fold_keeps_the_refs_of_a_sequence_born_point(prov):
    """#10010 review P2 — a fold must not NARROW a sequence-born prop.

    A Point created with a ref SEQUENCE carries an ARRAY ``extractedFrom``:
    that is the shared writer's own shape rule (``projection/entities.py``
    ``_upsert_point_props``, #3263 — *"an ARRAY whenever a SEQUENCE was
    passed — even a one-element one, which is NOT collapsed"*), and
    ``create_point`` stores the caller's props verbatim. The fold then chose
    ONE ref to keep and wrote it back, so ``[a, b]`` became ``'a'``: N-1 refs
    left a REPORTED surface (``get_point`` is ``properties(n)``;
    ``list_drafts``' ``provenance`` prefers this prop) and the stored property
    silently changed TYPE. That is the same class this PR exists to close —
    evidence disappearing from a reported surface — in the one shape no pin
    constructed: every other fold pin creates under a scalar.

    The fold must KEEP the shape the node had (the folded SET still rides
    ``extractedFromAll``, and the EDGES remain authoritative, #3263).

    MUTATION: restore the ``elif isinstance(_own, (list, tuple)): _primary_ref =
    next(...)`` arm and the first assertion below reds — the prop comes back
    ``'<a>'`` while ``b`` is gone.
    """
    sdk, events = prov
    proj = sdk._get_proj()
    a, b, c = (f"https://example.com/seq-{n}" for n in ("a", "b", "c"))
    content = "a claim whose provenance arrived as a ref sequence"
    pid = sdk.create_point("statement", content, extractedFrom=[a, b],
                           dedup=True)["id"]
    assert _prop_refs(proj, pid) == [a, b], (
        "guard: a sequence-create stores an ARRAY (the writer's shape rule): "
        f"{_prop_refs(proj, pid)!r}")

    sdk.create_point("statement", content, extractedFrom=c, dedup=True)
    assert _sources_of(proj, pid) == {a, b, c}, \
        "guard: the second write must be a fold that wires the third ref"

    kept = _prop_refs(proj, pid)
    assert isinstance(kept, list), (
        "the fold changed the stored property's TYPE — a sequence-born Point "
        f"kept an ARRAY before the fold, holds {type(kept).__name__} after")
    assert sorted(kept) == sorted([a, b]), (
        "the fold narrowed the Point's own refs — refs that origin/main "
        f"reported on this surface are gone: {kept!r}")
    assert sorted(_all_prop_refs(proj, pid)) == sorted([a, b, c]), (
        "the folded SET must still hold all three refs: "
        f"{_all_prop_refs(proj, pid)!r}")

    live = _render_row(proj, pid)
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _render_row(proj, pid) == live, (
        "live != replay on a sequence-born survivor: "
        f"live={live!r} replayed={_render_row(proj, pid)!r}")


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

    live = _render_row(proj, pid)
    assert live["sourceVersionTransit"] == [[one, "hash-one"],
                                            [two, "hash-two"]], live
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _render_row(proj, pid) == live, (
        "the journal does not reproduce the row live wrote — replay(journal) "
        f"!= live: live={live!r} replayed={_render_row(proj, pid)!r}")


def test_a_fold_with_no_recorded_versions_clears_the_carrier_and_replays(prov):
    """The EMPTY-``pairs`` arm — ``SET n.sourceVersionTransit = NULL``.

    The one carrier arm with no committed pin (#10010 review: "all carrier pins
    keep at least one pair"). ``_fold_source_evidence`` writes ``pairs or
    None``: a ref whose ``:Source`` records no ``contentHash`` contributes NO
    pair (honest-absent, ``_valid_transit_pairs``' rule), so a fold whose refs
    are all unversioned must write NULL — i.e. REMOVE the carrier, which is the
    state a replay of a payload that omits the key leaves behind
    (``_upsert_point_props`` sets only the keys its payload carries). Measured on
    the docker lane: ``SET n.p = NULL`` removes the key (``keys(n)`` no longer
    lists it), and an absent key and a stored null both read back as ``None`` —
    so live and replay agree in both directions.

    The seed is a STALE pair whose ref is neither an edge nor in the property —
    the shape the fold must DROP. Keeping it would leave live holding provenance
    the journal does not, i.e. ``derived != replay(journal)``; that is what the
    parity assertion below catches (a fold that stopped writing NULL leaves the
    seed in place: live holds the pair, the replay has none).
    """
    from tortoise.projection import _live_write_scope

    sdk, events = prov
    proj = sdk._get_proj()
    one, two = "https://example.com/v-one", "https://example.com/v-two"
    stale = "https://example.com/not-an-edge"
    content = "a fold whose refs carry no recorded read-version"
    pid = sdk.create_point("statement", content, extractedFrom=one,
                           dedup=True)["id"]
    assert _render_row(proj, pid)["sourceVersionTransit"] is None, \
        "guard: an unversioned Source must contribute NO carrier pair"
    # Deliberately UNJOURNALLED: a pair live holds and the journal does not is
    # exactly what the fold's NULL write has to clear.
    with _live_write_scope("test-seed"):
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.sourceVersionTransit = $pairs",
            params={"id": pid, "pairs": [[stale, "hash-stale"]]})
    assert _render_row(proj, pid)["sourceVersionTransit"] == [
        [stale, "hash-stale"]]

    sdk.create_point("statement", content, extractedFrom=two, dedup=True)
    assert _sources_of(proj, pid) == {one, two}, \
        "guard: the second write must be a fold, not a second Point"

    live = _render_row(proj, pid)
    assert live["sourceVersionTransit"] is None, (
        "the fold kept a carrier pair it did not journal — the empty-`pairs` "
        f"arm must REMOVE the property: {live['sourceVersionTransit']!r}")
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _render_row(proj, pid) == live, (
        "replay(journal) != live on the empty-`pairs` arm: "
        f"live={live!r} replayed={_render_row(proj, pid)!r}")


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


def test_a_fold_keeps_the_carrier_of_a_variant_spelled_held_ref(prov):
    """A held ref carried under a VARIANT spelling keeps its recorded read-version.

    The carrier is keyed by the RAW ``extractedFrom`` spelling (journal-stable —
    ``resolve_source_versions``) while ``present`` holds the edges' RESOLVED
    ``s.url``; the caller supplies a variant and the ``:Source`` is registered
    under the clean spelling, which is the ordinary connector shape. Deciding
    "the Point still holds this ref" on the RAW key therefore retains the pair
    only when the two spellings are byte-IDENTICAL, and every other variant
    silently drops a read-version the docstring of ``_fold_source_evidence``
    promises is "merged, never rebuilt".

    Frozen before (#7704 review P1, one line below its own fix): the fold's
    carrier came back ``[[two, hash-two]]`` — ``hash-one`` gone from both the
    live row and the replayed one, so the two agreed and no gate reported it.
    """
    sdk, events = prov
    proj = sdk._get_proj()
    clean = "https://example.com/one"
    raw = clean + "?utm_source=x"          # the connector's spelling
    second = "https://example.com/two"
    _seed_hashed_source(sdk, clean, "hash-one")
    _seed_hashed_source(sdk, second, "hash-two")

    content = "a variant-spelled held ref keeps its read-version"
    pid = sdk.create_point("statement", content, extractedFrom=raw,
                           dedup=True)["id"]
    assert _render_row(proj, pid)["sourceVersionTransit"] == [
        [raw, "hash-one"]], "guard: the create must record the held pair"

    sdk.create_point("statement", content, extractedFrom=second, dedup=True)
    assert _sources_of(proj, pid) == {clean, second}, \
        "guard: the second write must be a fold, not a second Point"

    assert _all_prop_refs(proj, pid) == [raw, second], (
        "the fold REWROTE the caller's provenance spelling into the "
        f"resolver's — the node owns that spelling: {_all_prop_refs(proj, pid)!r}")
    assert _prop_refs(proj, pid) == raw, (
        "the convenience property must keep the scalar spelling the Point was "
        f"created under: {_prop_refs(proj, pid)!r}")
    live = _render_row(proj, pid)
    assert live["sourceVersionTransit"] == [[raw, "hash-one"],
                                            [second, "hash-two"]], (
        "the fold DROPPED the recorded read-version of a ref the Point still "
        f"holds — merged, never rebuilt: {live['sourceVersionTransit']!r}")
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _render_row(proj, pid) == live, (
        "the journal does not reproduce the row live wrote — replay(journal) "
        f"!= live: live={live!r} replayed={_render_row(proj, pid)!r}")


def test_a_fold_keeps_the_carrier_when_the_nodes_spelling_was_changed(prov):
    """Held-ness is decided on the EDGE identity, not on the spelling.

    The shape that separates the two: the node's ``extractedFrom`` spelling is
    moved to another variant of the SAME Source before the fold (an
    ``update_point`` writes the prop; the edges still address the one
    ``:Source``). A retention test written against the spelling drops the held
    pair here — swallowing a recorded read-version for a ref the Point still
    holds — while an identity test keeps it, and the pair is re-keyed to the
    spelling this payload's ref list uses so the replay-side byte-wise selector
    (`_point_source_transit`) still matches it.

    Frozen before (#7704 review P1): the pair was dropped and only the new
    ref's read-version survived.
    """
    sdk, events = prov
    proj = sdk._get_proj()
    clean = "https://example.com/one"
    raw = clean + "?utm_source=x"
    moved = "https://EXAMPLE.com/one/"      # same identity, other spelling
    second = "https://example.com/two"
    _seed_hashed_source(sdk, clean, "hash-one")
    _seed_hashed_source(sdk, second, "hash-two")

    content = "a held ref survives a moved spelling"
    pid = sdk.create_point("statement", content, extractedFrom=raw,
                           dedup=True)["id"]
    sdk.update_point(pid, extractedFrom=moved)
    sdk.create_point("statement", content, extractedFrom=second, dedup=True)
    assert _sources_of(proj, pid) == {clean, second}, \
        "guard: the second write must be a fold, not a second Point"

    live = _render_row(proj, pid)
    assert live["sourceVersionTransit"] == [[moved, "hash-one"],
                                            [second, "hash-two"]], (
        "the fold dropped the read-version of a ref the Point still holds, "
        "because the two spellings differ — held-ness is an IDENTITY:"
        f" {live['sourceVersionTransit']!r}")
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _render_row(proj, pid) == live, (
        "a carrier key no longer named by the payload's own refs is dropped "
        "on replay — replay(journal) != live: "
        f"live={live!r} replayed={_render_row(proj, pid)!r}")


def test_a_fold_with_an_unhashed_ref_does_not_remove_the_carrier(prov):
    """Live == replay when the NEW ref records no hash at all — the loud arm.

    ``_source_version_transit`` omits a ref with no recorded hash (honest-absent),
    so if the held pairs were dropped too, ``pairs`` is empty and the live write
    ``SET … = NULL`` REMOVES ``sourceVersionTransit`` — while the journal record
    omits the key (an omitted property is RETAINED on replay: the pass-1 writer
    only sets the keys its payload carries) and the rebuild KEEPS the pair the
    create record installed. Live ≠ ``replay(journal)`` on the one prop this fold
    exists to journal, and ``check_consistency`` cannot see it.

    Frozen before (#7704 review P1): live ``None``, replayed
    ``[[raw, hash-one]]``.
    """
    sdk, events = prov
    proj = sdk._get_proj()
    clean = "https://example.com/one"
    raw = clean + "?utm_source=x"
    _seed_hashed_source(sdk, clean, "hash-one")

    content = "an unhashed new ref must not remove the carrier"
    pid = sdk.create_point("statement", content, extractedFrom=raw,
                           dedup=True)["id"]
    sdk.create_point("statement", content, session_id="sess-nohash",
                     dedup=True)
    assert _sources_of(proj, pid) == {clean, "session:sess-nohash"}, \
        "guard: the session ref must fold onto the same Point"

    live = _render_row(proj, pid)
    assert live["sourceVersionTransit"] == [[raw, "hash-one"]], (
        "the fold REMOVED the carrier of a Point that still holds the ref — "
        f"the journal record omits the key, so replay keeps it: {live!r}")
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _render_row(proj, pid) == live, (
        "derived = replay(journal) is FALSE on the fold's own carrier — "
        f"live={live!r} replayed={_render_row(proj, pid)!r}")


def test_a_fold_adopts_and_WIRES_a_prop_only_ref(prov):
    """A ref only the PROPERTY names is adopted into the wiring, not deleted.

    ``update_point(extractedFrom=…)`` writes the property and wires NO edge —
    the shape the dedup branch refuses for its own write, which is why the fold
    reads the edges in the first place — so a ref list rebuilt from the edges
    alone DELETED the caller's ref and left only an INFO line behind. On
    ``origin/main`` the dedup branch never touched the property, so the value
    survived: the fold must not be the thing that loses it. Adopting it is the
    #7704 ruling's direction ("one claim, N sources") — the list the fold
    writes is a SUPERSET of the property it replaces — and because the replayer's
    ``_upsert_point_edges`` calls the same ``_link_source``, live == replay.

    Frozen before (#7704 review P2): the property came back ``[held, second]``
    and the prop-only ref had no edge, no Source, and no record.
    """
    sdk, events = prov
    proj = sdk._get_proj()
    held = "https://example.com/held"
    prop_only = "https://example.com/prop-only"
    second = "https://example.com/second"
    content = "a prop-only ref is adopted by the fold"
    pid = sdk.create_point("statement", content, extractedFrom=held,
                           dedup=True)["id"]
    sdk.update_point(pid, extractedFrom=prop_only)
    assert _sources_of(proj, pid) == {held}, \
        "guard: update_point wires no edge, so the prop is the only name"
    assert _prop_refs(proj, pid) == prop_only, \
        f"guard: the property holds only the moved ref: {_prop_refs(proj, pid)!r}"

    sdk.create_point("statement", content, extractedFrom=second, dedup=True)
    assert _sources_of(proj, pid) == {held, prop_only, second}, (
        "the fold dropped a ref the caller had declared on the property (or "
        f"left it unwired): {_sources_of(proj, pid)}")
    live = _render_row(proj, pid)
    assert set(live["extractedFromAll"]) == {held, prop_only, second}, live
    assert live["extractedFrom"] == prop_only, (
        "the convenience property is the scalar the node carried when the fold "
        f"ran (the moved ref), not the folded set: {live['extractedFrom']!r}")
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _render_row(proj, pid) == live, (
        "live != replay after adopting a prop-only ref — the record's ref list "
        f"and the wired edges disagree: live={live!r} "
        f"replayed={_render_row(proj, pid)!r}")


def test_a_fold_survives_a_non_iterable_stored_prop(prov):
    """#10010 review P2: a stored scalar that is not a ``str`` must not CRASH
    the fold.

    ``update_point(extractedFrom=…)`` sets the property and validates nothing
    (``_validate_extracted_from`` is called only from ``create_point``,
    ``create_entity`` and ``create_document``), and an ``int`` is a LEGAL
    Cypher property value (the reference lists scalars — strings, integers,
    floats, booleans, temporals, points — and homogeneous lists as the
    storable types). So this shape is reachable from a public writer.

    Pre-fix, ``_fold_source_evidence`` special-cased only ``str`` and then ran
    ``for _ref in _prop_refs or []``, which raises
    ``TypeError: 'int' object is not iterable`` — BEFORE the journal append, so
    every subsequent ``create_point(..., dedup=True)`` against that point
    failed with an error naming neither the prop nor the door. ``origin/main``
    tolerated it (the dedup branch popped the incoming ref and warned), so this
    was a regression introduced by the fold.

    MUTATION: iterate the raw property again (``_prop_refs =
    existing.get("extractedFrom")`` with only ``str`` special-cased) and this
    test errors with that TypeError; the fold's own evidence must still land, so
    returning early instead of normalizing reds the ``_sources_of`` assertion.
    """
    sdk, events = prov
    proj = sdk._get_proj()
    held = "https://example.com/held"
    second = "https://example.com/second"
    content = "a non-iterable stored property must not break the fold"
    pid = sdk.create_point("statement", content, extractedFrom=held,
                           dedup=True)["id"]
    sdk.update_point(pid, extractedFrom=123)
    assert _prop_refs(proj, pid) == 123, (
        "guard: the writer really stored the non-string scalar: "
        f"{_prop_refs(proj, pid)!r}")

    folded = sdk.create_point("statement", content, extractedFrom=second,
                              dedup=True)
    assert folded["id"] == pid, "guard: this must be a dedup HIT"
    assert _sources_of(proj, pid) == {held, second}, (
        "the fold did not keep the survivor's evidence (or crashed and landed "
        f"nothing): {_sources_of(proj, pid)}")
    live = _render_row(proj, pid)
    assert set(live["extractedFromAll"]) == {held, second}, live
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _render_row(proj, pid) == live, (
        "live != replay after folding onto a non-string prop: "
        f"live={live!r} replayed={_render_row(proj, pid)!r}")


def test_a_fold_does_not_invent_refs_from_a_container_prop(prov, monkeypatch):
    """#10010 review P2 / #8171: a container-shaped prop must fan out to
    NOTHING, not to its keys.

    The fold re-implemented ref extraction instead of reusing
    ``projection.entities._point_source_refs`` — the helper whose docstring
    records the defect ("``list()`` on a mapping yields its KEYS, each a
    legal-looking string" → one invented ``:Source`` per key). The ``_adopted``
    arm then WIRES whatever it extracted, so an invented key became an invented
    provenance edge — "worse than none" (design contract #3).

    A mapping is not storable on this engine, so the shape is injected at the
    read boundary the fold uses (``get_point``), which is the only place it can
    arrive from — a legacy or record-sourced value the fold must not trust.
    The str/list/tuple arms are asserted alongside so the gate cannot be
    satisfied by refusing everything.

    MUTATION: iterate the raw property and the two invented keys appear in
    ``_sources_of`` (and as ``:Source`` nodes in ``_provenance``).
    """
    from tortoise.projection.entities import _point_source_refs

    sdk, _events = prov
    proj = sdk._get_proj()
    held = "https://example.com/held"
    second = "https://example.com/second"
    content = "a container-shaped prop must not invent sources"
    pid = sdk.create_point("statement", content, extractedFrom=held,
                           dedup=True)["id"]

    real_get_point = sdk.get_point
    poisoned = {"url": "https://invented.example/key", "kind": "invented"}

    def _get_point(point_id):
        point = real_get_point(point_id)
        if point is not None and point_id == pid:
            point = dict(point)
            point["extractedFrom"] = poisoned
        return point

    monkeypatch.setattr(sdk, "get_point", _get_point)
    sdk.create_point("statement", content, extractedFrom=second, dedup=True)

    invented = _sources_of(proj, pid) - {held, second}
    assert not invented, (
        "the fold wired refs that were only KEYS of a stored container — "
        f"invented provenance: {sorted(invented)}")
    assert _sources_of(proj, pid) == {held, second}, (
        f"the fold's real work must still land: {_sources_of(proj, pid)}")
    # The gate itself, on the shared helper the fold now calls.
    assert _point_source_refs(poisoned) == []
    assert _point_source_refs(200) == [] and _point_source_refs(True) == []
    assert _point_source_refs(held) == [held]
    assert _point_source_refs([held, None, ""]) == [held]


def _install_store_width_encoder(monkeypatch, sdk) -> int:
    """A deterministic encoder of the STORE's declared width, over the seam.

    ``create_point`` reaches its vector through ``tortoise.embeddings`` →
    ``encode_for_row`` → the module-global ``compute_embedding`` seam, so
    patching it here exercises the **server-computed** path: the write stores
    the vector and leaves ``embedding_verbatim`` ABSENT. That is what makes
    ``embedding_preserved`` load-bearing in
    ``test_a_fold_does_not_attest_the_embedding_origin_it_did_not_compute`` — a
    CALLER-supplied vector short-circuits ``stamp_journal_embedding`` on
    ``embedding_verbatim`` before it ever looks at the marker (#7704 review
    P2).

    ⛔ The width must be the STORE's, asked of the store, because
    ``_degrade_to_width`` DROPS a vector whose width is not
    ``required_embedding_dim`` (it does not truncate it). A hardcoded width is
    what made the pin this replaces pass only on the index-less embedded
    carve-out and fail on every store with a Point HNSW index (384) — green in
    the lane that hid the defect, red in CI (#7704 review P1).
    """
    from tortoise import embeddings as _emb
    from tortoise.embeddings import EMBEDDING_DIM

    width = sdk._get_proj().required_embedding_dim or EMBEDDING_DIM

    def _encode(content: str) -> list[float]:
        raw = hashlib.sha256(f"tortoise-7704::{content}".encode()).digest()
        buf = (raw * (width // len(raw) + 1))[:width]
        return [((byte / 255.0) - 0.5) for byte in buf]

    monkeypatch.setattr(_emb, "compute_embedding", _encode)
    return width


def test_a_fold_does_not_attest_the_embedding_origin_it_did_not_compute(
        prov, monkeypatch):
    """The re-emitted ``PointAdded`` PRESERVES the vector, never attests it.

    The fold computes no vector: ``snapshot["embedding"]`` is the node's
    EXISTING one, read back through ``get_point``. ``_emit_event`` stamps every
    ``PointAdded`` as a CREATING record, and ``stamp_journal_embedding`` then
    attests the ACTIVE embedder whenever the payload owns a vector and is not
    marked preserved — so a fold performed after a model change would journal
    ``embedding_model = <the new model>`` for a vector the OLD one computed, a
    FALSE origin that silences the R1 model-change warning by construction (that
    warning compares the JOURNALED identity against the configured embedder).
    The capture writer marks exactly this case (``embedding_preserved``,
    ``_write_capture_turns``).

    Frozen before (#7704 review P1): the fold's record carried
    ``embedding_model``/``embedding_revision``/``embedding_text_hash`` for a
    vector it never computed, and no ``embedding_preserved``.

    ⛔ The vector is SERVER-computed (a deterministic encoder of the store's own
    width over the ``compute_embedding`` seam), so ``embedding_verbatim`` is
    absent and ``embedding_preserved`` is the ONLY thing standing between the
    fold's record and a false attestation. That is the mutation this pin is
    for: drop the marker (``snapshot["embedding_preserved"] = True`` →
    ``if False``) and the identity keys appear, reddening the final assertion.
    With a caller-supplied vector the verbatim flag short-circuits first, the
    identity keys stay absent either way, and only the marker assertion reds —
    a pin on a marker that never exercises the behaviour it names (#7704 review
    P2). Measured before: the fixture seeded ``embedding=[0.5] * 8``, which the
    store's width guard drops whole, so the record carried NO vector at all and
    ``test (d)`` was red in the PR's own CI at this head.
    """
    import json

    sdk, events = prov
    log_path = os.path.join(str(events), "events.jsonl")
    width = _install_store_width_encoder(monkeypatch, sdk)
    first_ref, second_ref = ("https://example.com/emb-one",
                             "https://example.com/emb-two")
    content = "the fold must not attest an origin it did not compute"
    pid = sdk.create_point("statement", content, extractedFrom=first_ref,
                           dedup=True)["id"]
    stored = sdk.get_point(pid).get("embedding")
    assert stored is not None and len(stored) == width, (
        "guard: the create must STORE a vector of the store's own width "
        f"({width}) — the width guard drops a mismatch instead of truncating "
        f"it, and a fold cannot carry what the node does not hold: {stored!r}")
    sdk.create_point("statement", content, extractedFrom=second_ref,
                     dedup=True)

    with open(log_path) as fh:
        rows = [json.loads(ln) for ln in fh if ln.strip()]
    folds = [r for r in rows
             if r.get("type") == "PointAdded"
             and (r.get("point") or {}).get("id") == pid]
    assert len(folds) >= 2, (
        f"guard: the fold must emit a second PointAdded for {pid}: {len(folds)}")
    fold = folds[-1]["point"]
    assert fold.get("embedding") is not None, (
        "guard: this pin is about a fold that CARRIES the existing vector")
    assert not fold.get("embedding_verbatim"), (
        "guard: the load-bearing case is a SERVER-computed vector — with "
        "`embedding_verbatim` set, `stamp_journal_embedding` short-circuits "
        f"before it reads the marker: {sorted(fold)!r}")
    # The BEHAVIOUR first, the marker second: the marker exists to suppress
    # these three keys, and a pin that reds only on the marker cannot show that
    # the failure it names ever happened in its own fixture.
    for key in ("embedding_model", "embedding_revision", "embedding_text_hash"):
        assert key not in fold, (
            f"the fold attested {key} for a vector it never computed — a FALSE "
            "origin that silences the R1 model-change warning: " + repr(sorted(fold)))
    assert fold.get("embedding_preserved") is True, (
        "the fold's record is CREATING and holds a vector it did not compute — "
        "it must say the vector was PRESERVED: " + repr(sorted(fold)))


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
    assert _prop_refs(proj, pid) == "session:sess-dedup", (
        "the property must state one matchable ref — no prop-only lie in either "
        f"direction: {_prop_refs(proj, pid)!r}")
    assert _all_prop_refs(proj, pid) == ["session:sess-dedup"], (
        "the folded SET is written even for a one-ref fold, so the two surfaces "
        f"cannot be told apart by shape: {_all_prop_refs(proj, pid)!r}")

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


def test_a_tenant_cannot_supply_the_folded_ref_set(prov):
    """``extractedFromAll`` is the FOLD's own record — never a caller prop.

    The replay derives the ``extractedFrom`` EDGES from it (``_upsert_point_edges``
    → ``_point_all_source_refs``), so a tenant-supplied array would put the
    snapshot's edges ahead of the writes: live wires ``extractedFrom`` only, a
    rebuild wires the array too, and `derived = replay(journal)` goes false on a
    graph nobody folded — the same parity break the ``sourceVersionTransit``
    reject closes (#5256), which is why it joins that reject list instead of
    riding the open-set passthrough.
    """
    sdk, _events = prov
    before = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0]
    with pytest.raises(ValueError, match="server-managed provenance"):
        sdk.create_point(
            "statement", "a forged folded set",
            extractedFrom="https://example.com/x",
            extractedFromAll=["https://example.com/x",
                              "https://example.com/y"])
    assert sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0] == before, \
        "a rejected Point was still written (orphan)"


def test_a_re_creation_does_not_resurrect_the_folded_set(prov):
    """#7704 review F1 — a re-creation is a FRESH node, so the DEAD
    incarnation's folded set must not survive a rebuild.

    ``extractedFromAll``'s writer is PRESENCE-gated (``_upsert_point_props``
    emits ``SET n.extractedFromAll = $all`` only for a stated non-empty list)
    and a re-creation's payload can NEVER state one (only
    ``_fold_source_evidence`` writes it; ``create_point``'s ``_create_map`` has
    no such key). Pass-1a HOISTS the creation and MERGEs onto the dead
    incarnation's still-present node, whose ``tags``-sibling clauses preserve
    what the payload omits — so without the #4042 wipe entry the rebuilt node
    reports the dead set while LIVE reports none, i.e.
    ``derived = replay(journal)`` false on a reported surface (``get_point``
    returns ``properties(n)``), naming sources the new incarnation never
    carried.

    The re-creation RESTATES both refs on purpose: it keeps the ``extractedFrom``
    EDGE half out of the comparison (a dead edge surviving pass 2 is a recorded
    pre-existing residual, not this pin's subject), so the ONLY divergence the
    assertion can see is the folded-set property the wipe owns.
    """
    sdk, events = prov
    content = "a folded claim that is later deleted and reborn"
    alpha, beta = "https://example.com/alpha", "https://example.com/beta"
    first = sdk.create_point("statement", content, extractedFrom=alpha,
                             dedup=True)
    second = sdk.create_point("statement", content, extractedFrom=beta,
                              dedup=True)
    pid = first["id"]
    assert second["id"] == pid, "guard: the second write must FOLD"

    proj = sdk._get_proj()
    assert sorted(_all_prop_refs(proj, pid)) == sorted([alpha, beta]), (
        "guard: the fold must have accumulated the set, or this pin is "
        f"vacuous (got {_all_prop_refs(proj, pid)!r})")

    assert sdk.delete_point(pid) is True, "guard: the delete must land"
    sdk.create_point("statement", "a different claim under the same id",
                     id=pid, extractedFrom=[alpha, beta])
    assert _all_prop_refs(proj, pid) is None, (
        "guard: a re-creation states no folded set, so LIVE holds none "
        f"(got {_all_prop_refs(proj, pid)!r})")

    live = _render_row(proj, pid)
    proj.rebuild_all(str(events), confirm_destructive=True)
    assert _render_row(proj, pid) == live, (
        "the re-created node inherited the DEAD incarnation's folded set "
        "(extractedFromAll) on rebuild — live never had it, so "
        f"derived != replay(journal): live={live!r} "
        f"replayed={_render_row(proj, pid)!r}")
    assert _render_row(proj, pid)["extractedFromAll"] is None, \
        "the dead incarnation's folded set survived the recreate wipe"


def test_eventapi_add_point_rejects_a_forged_folded_set(prov):
    """#7704 review F2 — the one producer that bypasses ``_sanitize_props``.

    ``EventAPI.add_point`` is the internal ingest/mining/CLI seam and the SDK
    backstop never runs on it, so its own ``_forged`` set is the ONLY guard.
    Without the entry a caller-supplied array rides the ``PointAdded`` payload,
    and ``_upsert_point_edges``/``_point_all_source_refs`` derive the
    ``extractedFrom`` edges from it on replay — edges the live write never
    wired, so ``derived = replay(journal)`` goes false.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    sdk, events = prov
    api = EventAPI(EventLog(str(events / "events.jsonl")),
                   initiated_by="extractor", projection=sdk._get_proj())
    with pytest.raises(ValueError, match="server-managed"):
        api.add_point("forged folded set", {},
                      extractedFromAll=["https://example.com/x"])


def test_the_mcp_boundary_refuses_the_folded_set(prov):
    """#7704 review F2 — the key family is registered at EVERY home.

    ``_SERVER_MANAGED_PROPS`` is the fail-closed boundary IN FRONT of
    ``sdk._sanitize_props`` for every MCP entity tool (§5256's "Acceptance 2,
    at every home"). Defence in depth: the SDK backstop would still refuse the
    write, but the refusal would arrive AFTER the tool had begun work, and a
    future seam that skips the sanitizer would inherit no guard at all.
    """
    from tortoise import mcp_server

    assert "extractedFromAll" in mcp_server._SERVER_MANAGED_PROPS, (
        "extractedFromAll left the MCP server-managed boundary — the sibling "
        "`sourceVersion*` keys are registered here and the folded set is the "
        "same class of server-minted provenance record")
    assert mcp_server._reject_server_managed_props(
        {"extractedFromAll": ["https://example.com/x"]}), (
        "the MCP boundary accepted a caller-supplied folded set")


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


def test_a_fold_onto_a_dead_claim_keeps_its_terminalizer_stamp(prov):
    """#10010 review P1 — the fold's record is NOT the id's recreate anchor.

    ``_fold_source_evidence`` journals its work by re-emitting a ``PointAdded``
    for an id that ALREADY EXISTS, and the replay's recreate anchor refuses a
    terminalizer stamp at or before a creation ("it died with the replaced
    node"). A fold replaces NOTHING — and folding onto a DEAD claim is not
    exotic, it is authorised by this very branch: `#7819` is scoped to
    ``_TEMPORAL_RESTAMP_PROPS`` ("a fold that carries no temporal prop keeps
    applying, exactly like any other revision of a dead claim"), and
    ``_find_point_by_content`` applies no status/outdated filter. So the
    ordinary create → supersede → re-ingest-from-a-second-source sequence below
    lost the stamp in the rebuild: ``status``/``validTo`` came back from the
    fold's own snapshot, but ``outdated``/``expiredAt`` (``_POINT_DENY`` — no
    payload can restore them) and the CORRECTS edge did not, i.e. a DEAD claim
    served as live.

    Both terminalizers are pinned because they write different columns: a
    supersede sets ``status``+``outdated``+``expiredAt``+CORRECTS, an invalidate
    sets the ``outdated`` pair only. Frozen before the fix: the rebuilt node read
    ``{'status': 'superseded', 'outdated': None, 'expiredAt': None}``.
    """
    import json

    from tortoise.projection import EVIDENCE_FOLD_KEY

    sdk, events = prov
    proj = sdk._get_proj()
    a, b = "https://example.com/a", "https://example.com/b"
    superseded_content = "the expansion is funded through next spring"
    invalidated_content = "the expansion is funded through next autumn"

    superseded = sdk.create_point("statement", superseded_content,
                                  extractedFrom=a, dedup=True)["id"]
    successor = sdk.create_point("statement", "the expansion is funded",
                                 extractedFrom=a)["id"]
    sdk.supersede_point(superseded, successor)
    invalidated = sdk.create_point("statement", invalidated_content,
                                   extractedFrom=a, dedup=True)["id"]
    correction = sdk.create_point("statement", "the expansion is deferred",
                                  extractedFrom=a)["id"]
    sdk.invalidate_point(invalidated, correction)

    live = {pid: sdk.get_point(pid) for pid in (superseded, invalidated)}
    for pid, row in live.items():
        assert row.get("outdated") is True, (
            f"guard: the terminalizer must have stamped {pid} live", row)

    # The FOLD, onto each dead claim — a second source for the SAME content.
    for content in (superseded_content, invalidated_content):
        sdk.create_point("statement", content, extractedFrom=b, dedup=True)
    for pid in (superseded, invalidated):
        assert _sources_of(proj, pid) == {a, b}, (
            "guard: this must be a fold onto the dead claim, not a second "
            f"Point (sources={_sources_of(proj, pid)!r})")

    # The record says what it is — the anchor cannot infer "folded, created
    # nothing" from the payload's shape.
    with open(os.path.join(str(events), "events.jsonl")) as fh:
        records = [json.loads(ln) for ln in fh if ln.strip()]
    fold_records = [rec for rec in records
                    if rec.get("type") == "PointAdded"
                    and (rec.get("point") or {}).get(EVIDENCE_FOLD_KEY)]
    assert len(fold_records) == 2, (
        "the fold must FLAG the record it re-emits, or the replay reads it as "
        f"a creation: {[r.get('point', {}).get('id') for r in records]!r}")
    assert all(rec["point"].get("extractedFromAll") for rec in fold_records), (
        "guard: the flagged records must be the folds themselves")

    proj.rebuild_all(str(events), confirm_destructive=True)
    for pid, before in live.items():
        after = sdk.get_point(pid)
        assert after.get("outdated") is True, (
            "the rebuild dropped the terminalizer stamp and served a dead "
            f"claim as live: before={before!r} after={after!r}")
        assert after.get("expiredAt") == before.get("expiredAt"), (
            f"expiredAt lost across the rebuild: {before!r} -> {after!r}")
        assert after.get("status") == before.get("status"), (before, after)


def test_the_fold_record_flag_is_refused_at_every_prop_boundary(prov):
    """#10010 review P1 — the fold's RECORD flag is server-minted.

    A caller-supplied value is simultaneously a live/replay parity break (the
    flag is payload metadata, so it never persists as a node property live) and
    a FORGED replay decision (the recreate anchor reads it to decide whether a
    creation-shaped record replaced an incarnation). It therefore gets the
    ``embedding_preserved`` treatment: hard-rejected at the SDK prop intake, at
    ``EventAPI`` (the one producer that bypasses that intake), at the MCP
    boundary that sits in front of both, and at ``ingest``'s Phase-1 shape gate.
    """
    from tortoise import mcp_server
    from tortoise.api import EventAPI
    from tortoise.exceptions import BundleValidationError
    from tortoise.log import EventLog

    sdk, events = prov
    before = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0]
    with pytest.raises(ValueError, match="server-managed journal record flag"):
        sdk.create_point("statement", "a forged fold flag",
                         evidence_fold=True)
    assert sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)").result_set[0][0] == before, \
        "a rejected Point was still written (orphan)"

    api = EventAPI(EventLog(str(events / "events.jsonl")),
                   initiated_by="extractor", projection=sdk._get_proj())
    with pytest.raises(ValueError, match="server-managed"):
        api.add_point("a forged fold flag on the internal seam", {},
                      evidence_fold=True)

    assert "evidence_fold" in mcp_server._SERVER_MANAGED_PROPS, (
        "evidence_fold left the MCP server-managed boundary — the sibling "
        "`extractedFromAll` key is registered here and the flag is the same "
        "class of server-minted record fact")
    assert mcp_server._reject_server_managed_props({"evidence_fold": True}), (
        "the MCP boundary accepted a caller-supplied fold flag")

    # #10010 review P2: and at `ingest`'s Phase-1 shape gate — the fourth prop
    # boundary, and the one where a MISSING refusal is a partial mutation rather
    # than a fail-fast: `evidence_fold` is not a declared `create_point`
    # parameter, so a bundle item's key lands in `**props`, `_sanitize_props`
    # raises INSIDE step 3's write loop, and the earlier sections have already
    # committed. Both spellings — `_coerce_props` flattens the nested one onto
    # the same sanitizer. Asserted on the REFUSAL and on zero Points written,
    # because "refused at every prop boundary" is false if it costs an orphan.
    for spelling in ("top", "nested"):
        item = {"ref": "p1", "kind": "statement",
                "content": "a forged fold flag in a bundle"}
        if spelling == "top":
            item["evidence_fold"] = True
        else:
            item["props"] = {"evidence_fold": True}
        bundle = {"points": [item]}
        assert any("evidence_fold" in v["message"]
                   for v in sdk._validate_bundle(bundle)), (
            f"ingest's Phase-1 shape gate accepted the {spelling} spelling of "
            "evidence_fold — the refusal would then happen inside the write "
            "loop, after the earlier sections committed (partial mutation)")
        with pytest.raises(BundleValidationError, match="evidence_fold"):
            sdk.ingest(bundle)
        assert sdk._get_proj().g.query(
            "MATCH (n:Point) RETURN count(n)").result_set[0][0] == before, (
            f"the {spelling} spelling of evidence_fold committed a Point "
            "before the bundle was refused")
