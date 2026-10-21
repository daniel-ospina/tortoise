"""#9350 — ``capture_session``'s provenance stamp is a JOURNALLED write.

§17.1's third door (`proj.g` = the projection's write handle) refuses a cache
write no journal record describes. The capture path's provenance stamp — the
``SET n.eventId=…, n.source_session=…, n.source_harness=…, n.ingested_at=…``
over the ids THIS capture minted — was a raw write at that handle with **no
record**, so it was two defects wearing one cause:

1. **Durability.** The minted Point's own ``PointAdded`` snapshot is taken at
   extraction time, BEFORE the stamp, so it does not carry the four fields. A
   journal-only ``rebuild()`` therefore lost them for every minted Point that
   is never promoted (``_apply_capture_ingest_ep``'s ``PointPromoted``
   snapshot is what accidentally carried them for the promoted ones). The W2
   benchmark grades ``provenance_accuracy`` over exactly these fields.
2. **It blocked arming #9124 part 2.** #9206's rule (verbatim) is *"Where no
   record is appended, the honest outcomes are journal it or retire the path —
   a declaration that blesses an unjournalled cache write is the #9068
   defect."* The stamp is recoverable state, so it is JOURNALED here: one
   ``PointRevised`` per minted id, appended BEFORE the cache write, and the
   write then runs inside its own ``_live_write_scope`` lane.

What this suite pins:

* the record exists, per minted id, carrying all four fields;
* the record is in the journal BEFORE the statement reaches the handle (the
  ordering that makes the lane declaration honest — the same property
  ``tests/test_9124_declared_live_doors.py`` pins textually for whole doors);
* the statement is authorized by ITS OWN lane under an ARMED handle (the
  reason read back AT the write handle, not the outer scope's);
* ``rebuild()`` AND ``rebuild_all()`` restore the four fields on a minted
  Point that is never promoted;
* the hosted twin (`hosted_api._capture_session_impl`) does the same — the two
  lanes are byte-parity on these fields.
"""
from __future__ import annotations

import json
import os

import pytest

# The hosted-surface fixture: imported (not redefined) exactly as the other
# hosted parity suites do — the offline MockModel seam keeps it network-free.
from tests.test_hosted_api import client as client
from tortoise.log import EventLog
from tortoise.projection import _CACHE_WRITE_AUTHORIZED, _live_write_scope
from tortoise.sdk import _CAPTURE_PROVENANCE_LANE, TortoiseSDK

PROVENANCE = ("eventId", "source_session", "source_harness", "ingested_at")

#: The declared lane name, pinned as a LITERAL here — the `test_9079` pattern
#: (`EPOCH_LANE = "graph-metadata:ep-epoch"`, compared exactly).
#:
#: ⛔ The literal is the whole point: comparing the write handle's reason only
#: against the MODULE CONSTANT cannot see a rename, because the expectation and
#: the write move together — the docstring's "a rename is a red test rather than
#: a silent relabelling" would then be a claim nothing enforces. Both directions
#: redden here: a renamed constant fails
#: ``test_the_stamp_lane_literal_is_pinned``, and a write issued under any other
#: (even any other ``capture.*``) lane fails the read-back at the handle.
CAPTURE_LANE = "capture.provenance-stamp"

CONV = [
    {"role": "user",
     "content": "I think the auth dead-end is the top issue. "
                "We decided to ship serve --http first."},
    {"role": "assistant",
     "content": "Agreed. Evidence suggests the website config is the root "
                "cause."},
    {"role": "user", "content": "ok"},
]


@pytest.fixture(autouse=True)
def _offline_extractor(monkeypatch):
    """The offline MockModel extractor — zero network, zero provider spend."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture()
def un_promoted_capture(tmp_path, monkeypatch):
    """A capture whose minted points are NEVER promoted.

    ``_apply_capture_ingest_ep`` is what promotes a capture's minted claims —
    and its ``PointPromoted`` payload is a full ``get_point`` snapshot, which
    is exactly the accidental carrier that hid this defect for promoted
    points. Disabling it is what exposes the un-promoted minted Point the
    acceptance names.
    """
    import tortoise.sdk as sdk_mod

    monkeypatch.setattr(sdk_mod, "_apply_capture_ingest_ep",
                        lambda *a, **k: None)
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        res = sdk.capture_session(CONV)
        assert res["ok"] is True, res
        minted = [p["id"] for p in res["points"]]
        assert minted, "precondition: the capture must mint at least one Point"
        yield sdk, log_path, minted
    finally:
        sdk.close()


def _records(log_path):
    with open(log_path, encoding="utf-8") as fh:
        return [r for r in map(json.loads, (ln for ln in fh if ln.strip()))]


def _stamp_records(log_path, ids):
    return [r for r in _records(log_path)
            if r.get("type") == "PointRevised" and r.get("id") in set(ids)
            and all(k in r for k in PROVENANCE)]


def _point_props(proj, pid):
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": pid}).result_set
    assert rows, f"no :Point at {pid}"
    return rows[0][0] or {}


def test_the_capture_stamp_journals_one_record_per_minted_id(un_promoted_capture):
    """A record per minted id, carrying the four fields the graph holds."""
    sdk, log_path, minted = un_promoted_capture
    proj = sdk._get_proj()
    recs = {r["id"]: r for r in _stamp_records(log_path, minted)}
    assert set(recs) == set(minted), (
        "the capture's provenance stamp appended no record for "
        f"{sorted(set(minted) - set(recs))} — a raw write with no carrier is "
        "the #9068 shape (#9350)")
    for pid in minted:
        live = _point_props(proj, pid)
        for key in PROVENANCE:
            assert live.get(key), f"precondition: live {key!r} is stamped"
            assert recs[pid][key] == live[key], (
                f"the record's {key!r} ({recs[pid][key]!r}) is not what the "
                f"node holds ({live[key]!r}) — a record that describes a "
                "different write is worse than none")


def test_the_record_precedes_the_stamp_statement(tmp_path, monkeypatch):
    """JOURNAL FIRST — the record is in the journal before the cache write.

    This is the property a ``_live_write_scope`` declaration CLAIMS, and the
    one the issue's rule forbids pretending: an append after the write would
    let the derived store lead the truth. A FRESH SDK (not the fixture's) — a
    second capture of the same conversation folds by content hash and mints
    nothing, so it would exercise no stamp at all.
    """
    import tortoise.projection as projection
    import tortoise.sdk as sdk_mod

    monkeypatch.setattr(sdk_mod, "_apply_capture_ingest_ep", lambda *a, **k: None)
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    seen: list[dict] = []
    real_query = projection._GuardedGraph.query

    def _spy(self, cypher, *a, **k):
        if "n.eventId=$eid" in cypher:
            # The journal content AT the moment the stamp reaches the handle.
            seen.append({
                "lane": _CACHE_WRITE_AUTHORIZED.get(),
                "records": _stamp_records(
                    log_path, (k.get("params") or {}).get("ids") or []),
            })
        return real_query(self, cypher, *a, **k)

    monkeypatch.setattr(projection._GuardedGraph, "query", _spy)
    try:
        res = sdk.capture_session(CONV, session_id="sess_9350_ordering")
    finally:
        sdk.close()
    assert res["ok"] is True, res
    assert seen, "precondition: the stamp statement reached the handle"
    for hit in seen:
        assert hit["records"], (
            "the provenance stamp reached the write handle with NO record in "
            "the journal — the write leads the truth (#9350)")
        assert hit["lane"], (
            "the stamp ran with no declared lane: under an armed handle this "
            "is a DirectCacheWriteRefused (#9350 / #9124)")


def test_the_stamp_is_authorized_by_its_own_lane_under_an_armed_handle(
        tmp_path, monkeypatch):
    """ARMED handle: the capture completes and the stamp lane is its own.

    ⚠️ The OUTER scope stands in for the lanes #9349 declares (DRAFT at the
    time of writing: ``_write_session_and_turns``, ``_materialize_session_source``,
    ``_apply_capture_ingest_ep``, the entity edges …). It is a TEST-scope, not
    a production declaration — and the assertion that matters does not depend
    on it: the reason read back AT the stamp statement must be the capture's
    OWN lane, which an inner scope shadows the outer with. Delete the outer
    ``with`` once #9349 lands; the rest of this test is unaffected.
    """
    import tortoise.projection as projection
    import tortoise.sdk as sdk_mod

    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)
    monkeypatch.setattr(sdk_mod, "_apply_capture_ingest_ep", lambda *a, **k: None)
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        lanes: list[str | None] = []
        real_query = projection._GuardedGraph.query

        def _spy(self, cypher, *a, **k):
            if "n.eventId=$eid" in cypher:
                lanes.append(_CACHE_WRITE_AUTHORIZED.get())
            return real_query(self, cypher, *a, **k)

        monkeypatch.setattr(projection._GuardedGraph, "query", _spy)
        with _live_write_scope("test-standin: lanes #9349 declares"):
            res = sdk.capture_session(
                [{"role": "user",
                  "content": "The on-call rota is the decision: we rotate "
                             "weekly and page the secondary first."},
                 {"role": "assistant", "content": "Agreed, weekly rotation."}],
                session_id="sess_9350_armed")
        assert res["ok"] is True, res
    finally:
        sdk.close()
    assert lanes, "precondition: the armed capture reached the stamp statement"
    assert all(lane and lane.startswith("capture.") for lane in lanes), (
        "the stamp statement ran under "
        f"{lanes!r} — not under the capture's own lane, so the write is only "
        "authorized by whatever scope the caller happened to hold (#9350)")
    # The EXACT lane, compared against the LITERAL pinned in this test (not only
    # against the module constant): a prefix check passes on any relabelling
    # inside the lane family, and a constant-only comparison follows a rename
    # silently (#9350 review).
    assert set(lanes) == {CAPTURE_LANE}, (
        "the stamp statement ran under "
        f"{lanes!r} — not under {CAPTURE_LANE!r}, so the write is only "
        "authorized by whatever scope the caller happened to hold, or the lane "
        "was renamed (#9350)")
    records = [r for r in _records(log_path) if r.get("type") == "PointRevised"]
    assert records, "the capture appended no provenance record (#9350)"


def test_the_stamp_lane_literal_is_pinned():
    """The module constant IS the pinned literal — a rename reddens here.

    ``sdk._CAPTURE_PROVENANCE_LANE``'s docstring says the literal is stable ON
    PURPOSE and that a rename is a red test rather than a silent relabelling.
    The read-back above compares the lane at the write handle against
    ``CAPTURE_LANE``; this binds the module constant to that same literal, so
    the docstring's claim is one this suite ENFORCES rather than restates. A
    constant-only comparison (no literal in the test) would move with the
    rename and stay green.
    """
    assert _CAPTURE_PROVENANCE_LANE == CAPTURE_LANE, (
        f"the capture stamp's lane constant is "
        f"{_CAPTURE_PROVENANCE_LANE!r}, but the pinned literal is "
        f"{CAPTURE_LANE!r} — a renamed lane is a silent relabelling for every "
        "operator reading a DirectCacheWriteRefused message (#9350)")


@pytest.mark.parametrize("engine", ["rebuild", "rebuild_all"])
def test_a_rebuild_restores_the_provenance_of_an_unpromoted_point(
        un_promoted_capture, engine):
    """`derived == replay(journal)` for the four fields (#9350 acceptance)."""
    sdk, log_path, minted = un_promoted_capture
    proj = sdk._get_proj()
    before = {pid: _point_props(proj, pid) for pid in minted}
    for pid, props in before.items():
        for key in PROVENANCE:
            assert props.get(key), (
                f"precondition: live {key!r} of {pid} must be stamped")

    if engine == "rebuild":
        proj.rebuild(EventLog(log_path), confirm_destructive=True)
    else:
        proj.rebuild_all(str(os.path.dirname(log_path)),
                         confirm_destructive=True)

    after = {pid: _point_props(proj, pid) for pid in minted}
    for pid in minted:
        for key in PROVENANCE:
            assert after[pid].get(key) == before[pid][key], (
                f"{engine}() lost {key!r} on minted point {pid}: "
                f"{before[pid].get(key)!r} -> {after[pid].get(key)!r} "
                "(the #9350 defect — an un-promoted minted Point's "
                "provenance is not in the journal)")


# ── the write is narrowed to the JOURNALLED set (#9350) ───────────────────

def test_the_stamp_write_cannot_touch_an_id_that_has_no_record(
        tmp_path, monkeypatch):
    """The guarded stamps write ONLY the ids that carry a record.

    A guarded stamp's ``$ids`` is the caller's CLAIM predicate (minted ids), not
    the set this call journalled (the ids the pre-read matched). Without the
    ``AND <node>.id IN $stamp_ids`` narrow clause an id that became eligible
    between the pre-read and the write would be stamped with NO record — the
    unjournalled write #9350 exists to remove, one race away. This pins the
    bound values directly, so removing the clause or the binding reds here.
    """
    from tortoise.sdk import _stamp_capture_provenance

    sdk = TortoiseSDK(str(tmp_path / "stamp.db"))
    try:
        proj = sdk._get_proj()
        seen = {}
        real_query = proj.g.query

        def _spy(self, cypher, *a, **k):
            if "$stamp_ids" in cypher:
                seen["cypher"] = cypher
                seen["params"] = k.get("params") or {}
            return real_query(cypher, *a, **k)

        monkeypatch.setattr(type(proj.g), "query", _spy)
        ok = _stamp_capture_provenance(
            sdk, proj, ["p-a", "p-b"], event_id="evt-1",
            session_id="sess-1", harness="tortoise-sdk",
            ingested_at="2026-10-20T00:00:00Z",
            statement=("MATCH (o:Point) WHERE o.id IN $ids "
                       "AND o.eventId IS NULL AND o.id IN $stamp_ids "
                       "SET o.eventId=$eid"),
            what="test stamp",
            params={"ids": ["p-a", "p-b", "p-c"]})
    finally:
        sdk.close()
    assert ok is True
    assert seen, "precondition: the stamp statement reached the write handle"
    assert seen["params"]["ids"] == ["p-a", "p-b", "p-c"], (
        "the caller's claim predicate must survive unchanged")
    assert seen["params"]["stamp_ids"] == ["p-a", "p-b"], (
        "the write was bound to "
        f"{seen['params'].get('stamp_ids')!r} — not to the ids this call "
        "journalled, so an id can be written with no record (#9350)")


# ── a REFUSED record must not be followed by the cache write ──

def test_a_refused_record_skips_the_cache_write(tmp_path, monkeypatch):
    """The derived store must never lead the truth (§17.1).

    ``_emit_event`` returns ``False`` when a CONFIGURED journal refused the
    append. The stamp must then skip the cache write: a stamp with no record is
    exactly the defect #9350 removes, and keeping the write on the failed-append
    path would reintroduce it one branch away. The capture itself still
    succeeds — the refusal is counted and disclosed by the receipt (#4240).
    """
    import tortoise.sdk as sdk_mod

    monkeypatch.setattr(sdk_mod, "_apply_capture_ingest_ep", lambda *a, **k: None)
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        real_emit = sdk._emit_event

        def _emit(type_, *a, **fields):
            if type_ == "PointRevised":
                return False          # a configured journal refused it
            return real_emit(type_, *a, **fields)

        monkeypatch.setattr(sdk, "_emit_event", _emit)
        res = sdk.capture_session(CONV)
        assert res["ok"] is True, res
        minted = [p["id"] for p in res["points"]]
        assert minted, "precondition: the capture minted a Point"
        props = _point_props(sdk._get_proj(), minted[0])
    finally:
        sdk.close()
    assert not props.get("eventId"), (
        "the stamp wrote eventId="
        f"{props.get('eventId')!r} although its record was REFUSED — the "
        "derived store now leads the journal with no record describing the "
        "write (the #9350/#9068 shape)")


# ── the pure fold's creation arm (#9350: parity with the graph fold) ──────

def _add_point(points, **point):
    from tortoise.projection import _apply_one
    _apply_one(points, {"type": "PointAdded", "point": point})


_STAMPED = {
    "eventId": "evt-9350-1",
    "source_session": "sess-9350-1",
    "source_harness": "tortoise-sdk",
    "ingested_at": "2026-10-20T00:00:00Z",
}


def test_the_pure_fold_applies_a_provenance_revision():
    """The ``PointRevised`` arm of ``_apply_one`` — ``fold()`` parity (#330).

    ``rebuild``/``rebuild_all`` both go through the GRAPH fold, so the pure
    fold's revision arm has no coverage from them: an empty arm would leave
    `fold()` (and the `check_consistency` oracle) disagreeing with the rebuilt
    graph on the same journal, which is what the #9350 stamp is replayed BY.
    """
    from tortoise.projection import _apply_one

    points: dict = {}
    _add_point(points, id="p1", content="a", context="ctx")
    _apply_one(points, {"type": "PointRevised", "id": "p1", **_STAMPED})
    for key in PROVENANCE:
        assert points["p1"].get(key) == _STAMPED[key], (
            f"the pure fold dropped the revision's {key!r} — fold() and "
            "rebuild_all would disagree on the same journal (#330)")


def test_a_provenance_revision_is_presence_conditional():
    """A revision that states only some fields changes only those."""
    from tortoise.projection import _apply_one

    points: dict = {}
    _add_point(points, id="p1", content="a", context="ctx", **_STAMPED)
    _apply_one(points, {"type": "PointRevised", "id": "p1",
                        "eventId": "evt-9350-2"})
    assert points["p1"]["eventId"] == "evt-9350-2"
    for key in PROVENANCE[1:]:
        assert points["p1"].get(key) == _STAMPED[key], (
            f"a revision that did not state {key!r} CLEARED it — the pure "
            "fold states absence as a clear, live does not (#9350)")


def test_a_later_creation_that_omits_a_provenance_key_keeps_it():
    """#330 parity: the pure fold must not DROP what the graph fold KEEPS.

    `_upsert_point_props` writes only the keys a creation payload states (and
    skips a key stated as `None`), so LIVE keeps a stored `eventId` when a
    later re-emit restates only `content`. Replacing the entry outright in
    `_apply_one` would drop it, and `fold()` / `rebuild_all` would disagree on
    the same journal — the asymmetry the `tags` carry-forward already closes
    for tags (the capture turn loop re-emits `PointAdded` with a hand-built
    snapshot, which is exactly the omitted-key shape).
    """
    points: dict = {}
    _add_point(points, id="p1", content="first", context="ctx", **_STAMPED)
    _add_point(points, id="p1", content="second", context="ctx")
    assert points["p1"]["content"] == "second"
    for key in PROVENANCE:
        assert points["p1"].get(key) == _STAMPED[key], (
            f"the pure fold dropped {key!r} on a re-emit that did not restate "
            "it, while the graph fold keeps it (#9350/#330)")


def test_a_re_creation_does_not_inherit_a_dead_incarnations_provenance():
    """The carry-forward is gated on the #4042 recreate boundary.

    A hard delete→recreate is a FRESH node live, so the fold must not hand it
    the deleted incarnation's `eventId` — the same rule the `tags`
    carry-forward follows.
    """
    from tortoise.projection import _apply_one
    points: dict = {}
    _add_point(points, id="p1", content="first", context="ctx", **_STAMPED)
    _apply_one(points, {"type": "PointAdded",
                        "point": {"id": "p1", "content": "reborn",
                                  "context": "ctx"}},
               journal_recreate=True)
    assert points["p1"]["content"] == "reborn"
    for key in PROVENANCE:
        assert key not in points["p1"], (
            f"a re-created node inherited {key!r} from the dead incarnation")


def test_a_rebuild_all_of_a_re_creation_drops_the_dead_provenance(
        un_promoted_capture):
    """The PASS-1A WIPE honours the boundary — the graph half of the gate.

    ``rebuild`` is chronological (the delete really removes the node), so it
    never reaches the recreate branch; ``rebuild_all`` HOISTS every creation and
    folds the delete after, so a re-creation MERGEs onto the dead incarnation's
    still-present node while `_upsert_point_props` writes only the keys the
    payload states. Without the pass-1a wipe the rebuilt node keeps the dead
    incarnation's `eventId` while LIVE holds none — `derived != live` AND
    `fold() != rebuild_all` on the same journal, with a `check_consistency` red
    whose own action ("replay the journal") reproduces it.

    The provenance is put in the CREATION PAYLOAD, not left to the capture
    stamp: that is the shape `rebuild_all` actually replays here (and the shape
    a migrated graph produces from `properties(n)` synthetic events), so the
    test reds when the wipe or the per-prop skip boundary is removed.
    """
    sdk, log_path, minted = un_promoted_capture
    pid = minted[0]
    recs = _records(log_path)
    recs.extend([
        {"event_id": "ev_add_9350", "ts": "2026-10-20T00:00:00+00:00",
         "type": "PointAdded", "initiated_by": "raw-producer",
         "projection_version": 2,
         "point": {"id": pid, "content": "first", "status": "live",
                   **_STAMPED}},
        {"event_id": "ev_del_9350", "ts": "2026-10-20T00:00:01+00:00",
         "type": "EntityMutated", "op": "delete", "id": pid,
         "label": "Point", "initiated_by": "raw-producer",
         "projection_version": 2},
        {"event_id": "ev_reborn_9350", "ts": "2026-10-20T00:00:02+00:00",
         "type": "PointAdded", "initiated_by": "raw-producer",
         "projection_version": 2,
         "point": {"id": pid, "content": "reborn", "status": "live"}},
    ])
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in recs) + "\n")

    sdk._get_proj().rebuild_all(str(os.path.dirname(log_path)),
                               confirm_destructive=True)

    props = _point_props(sdk._get_proj(), pid)
    assert props.get("content") == "reborn", "precondition: the recreate folded"
    for key in PROVENANCE:
        assert props.get(key) is None, (
            f"rebuild_all() gave the re-created node {key!r}={props.get(key)!r} "
            "— the dead incarnation's provenance, which live never had "
            "(#9350)")


def test_a_value_the_store_cannot_hold_is_not_carried():
    """Carry-forward mirrors the WRITER's OUTCOME, not the key's presence.

    Three shapes, three different live outcomes — all of them decided by a
    predicate, none by ``is None``:

    * a value ``_is_persistable_prop_value`` rejects (a map) is a NO-WRITE in
      ``_persist_extra_props``, so the stored value survives;
    * a value the ``#7369`` transport gate refuses (a NUL string) is degraded
      to ``SET n.<k> = NULL`` before dispatch, which REMOVES the property;
    * a value whose fate is ENGINE-DEPENDENT (an ``int`` outside INT64 —
      4.16–4.20 CLAMPS it, 6.x refuses it at parameter parse) is written by no
      consistent outcome at all, so the fold contributes NO property.

    Carrying on ``is None`` alone got the first two wrong (dict kept the junk,
    the NUL string kept a value live dropped). ⚠️ The third's reason is
    deliberately NOT "nulled at dispatch": ``_value_ok(2**70)`` is ``True``
    (measured), so the transport gate does NOT null it — the bar is
    ``_store_holds_prop_value``'s "the store holds it exactly as stated", the
    same one the ``tags`` clause and ``consistency._uncarried`` use.
    """
    points: dict = {}
    _add_point(points, id="p1", content="first", context="ctx", **_STAMPED)
    _add_point(points, id="p1", content="second", context="ctx",
               eventId={"not": "a property"},
               source_harness="bad\x00harness",
               ingested_at=2 ** 70)
    assert points["p1"].get("eventId") == _STAMPED["eventId"], (
        "a value the writer cannot persist is a NO-WRITE, so the stored "
        "provenance survives — the fold overwrote it with junk")
    assert "source_harness" not in points["p1"], (
        "a value the #7369 transport gate refuses (a NUL string) is degraded "
        "to `SET n.<k> = NULL` at dispatch, which REMOVES the property live; "
        "the fold kept the raw value")
    assert "ingested_at" not in points["p1"], (
        "an out-of-INT64 value has no uniform live outcome (4.16–4.20 clamps "
        "it, 6.x refuses it at parameter parse), so the fold must contribute "
        "NO property — it kept the raw value the store never holds")


def test_the_revision_fold_drops_a_value_the_store_cannot_hold():
    """The ``PointRevised`` arm obeys the same bar as the creation arm.

    ``eventId`` through ``ingested_at`` are free-form scalars, so
    ``_annotator_value_ok`` alone admits an ``int`` outside INT64 (measured:
    ``_annotator_value_ok(2**70)`` is ``True`` while
    ``_store_holds_prop_value(2**70)`` is ``False``). Folding it made
    ``fold()`` hold ``2**70`` while ``rebuild_all`` clamped it to
    ``9223372036854775807`` (or aborted at parameter parse on 6.x), so the
    two engines disagreed on the same journal and the recovery could not be
    cleared by the action ``check_consistency`` recommends.
    """
    from tortoise.projection import _apply_one

    points: dict = {}
    _add_point(points, id="p1", content="a", context="ctx")
    _apply_one(points, {"type": "PointRevised", "id": "p1",
                        "eventId": "evt-9350-ok",
                        "ingested_at": 2 ** 70})
    assert points["p1"].get("eventId") == "evt-9350-ok", (
        "the store-holdable field must still fold — the gate drops the "
        "value, not the record")
    assert "ingested_at" not in points["p1"], (
        "the fold kept an INT64-overflowing value the store would clamp or "
        "refuse; fold() and rebuild_all would disagree on the same journal")


def test_a_rebuild_drops_a_provenance_value_the_store_cannot_hold(tmp_path):
    """The GRAPH half of the same gate — the arm that can abort a recovery.

    ``_revise_point`` emits ``n.<prop> = $<prop>`` for a record it admits. Emit
    it for an out-of-INT64 value and a 6.x engine RAISES at parameter parse
    AFTER pass 1a's wipe has already run (#325/#3689: the recovery cannot be
    re-run), while 4.16–4.20 silently clamps it and the pure fold keeps the raw
    value — `derived != live` with an unclearable `check_consistency` red. So
    the clause must be skipped for a value the store cannot hold, exactly as
    the ``tags`` clause beside it does.

    A journal-ONLY graph: no prior live node, so ``rebuild_all``'s synthetic
    fold has no property snapshot to re-supply the value from (that snapshot is
    what a capture-derived fixture would replay instead of the revision under
    test).
    """
    events = tmp_path / "events"
    events.mkdir()
    recs = [
        {"event_id": "ev_add_9350", "ts": "2026-10-20T00:00:00+00:00",
         "type": "PointAdded", "initiated_by": "raw-producer",
         "projection_version": 2,
         "point": {"id": "p1", "content": "c", "context": "ctx",
                   "status": "live"}},
        {"event_id": "ev_badprov_9350", "ts": "2026-10-20T00:00:01+00:00",
         "type": "PointRevised", "id": "p1", "initiated_by": "raw-producer",
         "projection_version": 2, "eventId": "evt-9350-injected",
         "ingested_at": 2 ** 70},
    ]
    (events / "sdk.jsonl").write_text(
        "\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")

    sdk = TortoiseSDK(str(tmp_path / "rebuild.db"))
    try:
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        props = _point_props(sdk._get_proj(), "p1")
    finally:
        sdk.close()
    assert props.get("eventId") == "evt-9350-injected", (
        "precondition: the revision's store-holdable field must replay")
    assert props.get("ingested_at") is None, (
        f"rebuild_all() wrote ingested_at={props.get('ingested_at')!r} from an "
        "out-of-INT64 record: clamped on 4.16–4.20 and a parse abort after "
        "the wipe on 6.x (#325/#3689)")


# ── the hosted twin (#9350 acceptance: both lanes are byte-parity) ─────────

def test_the_hosted_capture_journals_and_replays_the_same_provenance(
        client, tmp_path, monkeypatch):
    """``hosted_api._capture_session_impl`` is the SDK lane's twin.

    It duplicates the stamp verbatim (byte-parity #1727/#2104), so it carried
    the same defect and takes the same fix: the shared
    ``_stamp_capture_provenance`` door, one record per minted id BEFORE the
    write. The journal is supplied through ``_get_event_log`` because the test
    client's SDK instances are built by the hosted factory (which configures no
    journal); a deployment's does, and that is the lane being pinned.
    """
    import tortoise.hosted_api as ha_mod
    from tests.test_hosted_api import TEST_ORG_ID
    from tortoise.log import EventLog

    log_path = str(tmp_path / "hosted.jsonl")
    monkeypatch.setattr(
        ha_mod.TortoiseSDK, "_get_event_log",
        lambda self: EventLog(log_path))

    r = client.post("/v1/sessions", json={"conversation": [
        {"role": "user",
         "content": "The release freeze is the decision: no deploys until the "
                    "rebuild has been rehearsed twice."},
        {"role": "assistant", "content": "Agreed, freeze until rehearsed."},
    ]})
    assert r.status_code == 200, r.text

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.source_session IS NOT NULL "
        "RETURN n.id, properties(n)").result_set
    assert rows, (
        "precondition: the hosted capture stamped no Point (the provenance "
        "stamp gates over the MINTED ids)")
    before = {pid: props for pid, props in rows}

    records = {rec.get("id"): rec for rec in _records(log_path)
               if rec.get("type") == "PointRevised"
               and all(k in rec for k in PROVENANCE)}
    missing = sorted(set(before) - set(records))
    assert not missing, (
        f"the hosted capture appended no provenance record for {missing} — "
        "the twin lane kept the raw, unjournalled stamp (#9350)")
    for pid in before:
        for key in PROVENANCE:
            assert records[pid].get(key) == before[pid].get(key), (
                f"hosted {key!r} for {pid}: record "
                f"{records[pid].get(key)!r} != node {before[pid].get(key)!r}")

    proj.rebuild(EventLog(log_path), confirm_destructive=True)
    for pid in before:
        after = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN properties(n)",
            params={"id": pid}).result_set
        assert after, f"the hosted capture's Point {pid} did not replay"
        for key in PROVENANCE:
            assert after[0][0].get(key) == before[pid].get(key), (
                f"rebuild() lost hosted {key!r} on {pid}: "
                f"{before[pid].get(key)!r} -> {after[0][0].get(key)!r}")


# ── the guard rails the two lanes must not lose (review round 3) ──────────

def test_a_guarded_stamp_without_the_narrow_clause_is_refused(tmp_path):
    """A guarded stamp MUST narrow its write to the journalled ids.

    ``$ids`` on a guarded statement is the caller's CLAIM predicate, so without
    ``AND <node>.id IN $stamp_ids`` the written set is not a subset of the
    recorded set and an id that became eligible between the pre-read and the
    write is stamped with no record. The helper refuses that shape itself, so a
    call site that drops the clause (either lane) fails loudly rather than
    reverting one race away from §17.1.
    """
    from tortoise.sdk import _stamp_capture_provenance

    log_path = str(tmp_path / "events" / "guard.jsonl")
    sdk = TortoiseSDK(str(tmp_path / "guard.db"), event_log_path=log_path)
    try:
        proj = sdk._get_proj()
        with pytest.raises(ValueError, match=r"\$stamp_ids"):
            _stamp_capture_provenance(
                sdk, proj, ["p-a"], event_id="evt-1", session_id="sess-1",
                harness="tortoise-sdk", ingested_at="2026-10-20T00:00:00Z",
                statement="MATCH (o:Point) WHERE o.eventId IS NULL "
                          "SET o.eventId=$eid",
                what="unguarded test stamp")
    finally:
        sdk.close()
    # ⛔ The refusal must come BEFORE the appends: a raise ordered after them
    # leaves the journal holding records for a write that never happened, and a
    # journal-only rebuild then applies provenance the capture never wrote — the
    # mirror of the defect this issue removes (found in review round 3).
    residue = ([r for r in _records(log_path)
                if r.get("type") == "PointRevised" and r.get("id") == "p-a"]
               if os.path.exists(log_path) else [])
    assert not residue, (
        "the refused stamp still journalled a record for p-a, so a rebuild "
        f"would apply provenance that was never written: {residue!r}")


def test_the_id_pre_read_refuses_a_statement_that_writes(tmp_path):
    """The census declares this helper an unresolved READ — enforce it.

    The §17.1 arming gate rests on that declaration, so a ``SET`` smuggled into
    a caller's prefix would ride a declaration that says "reads" — the census
    false-PASS the review measured.
    """
    from tortoise.sdk import _capture_stamp_targets

    sdk = TortoiseSDK(str(tmp_path / "readonly.db"))
    try:
        with pytest.raises(ValueError, match="read-only"):
            _capture_stamp_targets(
                sdk, sdk._get_proj(),
                "MATCH (n:Point) RETURN DISTINCT n.id "
                "SET n.eventId='LEAK' REMOVE n.x",
                {"ids": ["p-a"]}, "writing test read")
    finally:
        sdk.close()


def test_the_id_pre_read_rides_the_write_retry_funnel(tmp_path):
    """A graph-replaced abort on the READ must not lose the whole stamp (#7405).

    The pre-read is on the same stamp path as the write, so it goes through the
    same funnel: a transient abort re-issues it instead of raising out of the
    capture and leaving the minted claims unstamped.
    """
    import redis.exceptions as redis_exc

    from tortoise.sdk import _capture_stamp_targets

    class _AbortOnce:
        def __init__(self, real, needle):
            self._real, self._needle = real, needle
            self.matching = 0
            self.applied = 0

        def query(self, cypher, params=None, timeout=None):
            if self._needle in cypher:
                self.matching += 1
                if self.matching == 1:
                    raise redis_exc.ResponseError(
                        "graph was deleted or replaced while the query was "
                        "running, aborting")
                self.applied += 1
            return self._real.query(cypher, params=params, timeout=timeout)

        def __getattr__(self, name):
            return getattr(self._real, name)

    sdk = TortoiseSDK(str(tmp_path / "preread.db"))
    try:
        pid = sdk.create_point(
            "statement", "a claim the pre-read must find")["id"]
        proj = sdk._get_proj()
        proxy = _AbortOnce(proj.g, "RETURN DISTINCT")
        proj.g = proxy
        try:
            ids = _capture_stamp_targets(
                sdk, proj,
                "MATCH (n:Point) WHERE n.eventId IS NULL "
                "RETURN DISTINCT n.id",
                {"ids": [pid]}, "pre-read retry test")
        finally:
            proj.g = proxy._real
        assert ids == [pid], f"the re-issued read returned {ids!r}"
        assert proxy.matching >= 2, (
            "the aborted pre-read was never re-issued, so a graph-replaced "
            "abort loses the whole provenance stamp (#7405/#9350)")
    finally:
        sdk.close()


@pytest.mark.parametrize("module", ["tortoise/sdk.py", "tortoise/hosted_api.py"])
def test_every_guarded_stamp_call_narrows_its_write_to_the_journalled_ids(module):
    """EVERY guarded call site, both lanes — the narrowing is checked in source.

    The helper refuses the shape at RUNTIME, but a guarded site that drops the
    clause only fails when that path is actually executed — and the operator and
    retry-heal paths are not reached by every capture. This walks the two
    modules instead, so dropping the clause from any of the six sites (three per
    lane) reds CI rather than reverting one race away from §17.1. The minted
    stamp is UNGUARDED and passes no ``params`` at all, so it is correctly
    exempt — and the BUCKET SIZES are asserted, because a classifier that
    quietly files a guarded site under `unguarded` would exempt it silently
    (measured: keying on `"ids"` alone missed both retry-heal sites, which pass
    `params={"sid": …}`).
    """
    import ast
    from pathlib import Path

    src = Path(module).read_text(encoding="utf-8")
    tree = ast.parse(src)
    guarded, unguarded = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name != "_stamp_capture_provenance":
            continue
        segment = ast.get_source_segment(src, node) or ""
        (guarded if "params=" in segment else unguarded).append(segment)
    assert len(guarded) == 3, (
        f"{module}: expected the three GUARDED stamp sites (operator label "
        f"sweep, operator ids, retry heal), found {len(guarded)} — a site that "
        "moved or lost its `params` now escapes the pin below")
    assert len(unguarded) == 1, (
        f"{module}: expected exactly ONE unguarded site (the minted-point "
        f"stamp), found {len(unguarded)}")
    for segment in guarded:
        assert "$stamp_ids" in segment, (
            f"{module}: a guarded _stamp_capture_provenance call does not "
            "narrow its write to $stamp_ids, so the WRITTEN set is not a "
            "subset of the JOURNALLED set — an id that becomes eligible "
            "between the pre-read and the write is stamped with no record "
            "(#9350):\n" + segment[:600])
