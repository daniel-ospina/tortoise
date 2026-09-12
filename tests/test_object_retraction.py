# tests/test_object_retraction.py
import json
import shutil
import pytest
from tortoise.log import EventLog     # CYCLE 7: hoisted here from Task 8.
                                     # `EventLog` and `_drive` are used by
                                     # tests in EVERY task, so they must be
                                     # defined with the file, not at the end
                                     # (an implementer working task-by-task
                                     # hit `NameError` at Task 3 Step 4, whose
                                     # Expected says PASS). tortoise/event_log.py
                                     # does NOT exist — the module is
                                     # `tortoise.log`.
from tortoise.consistency import recover_from_log
from tortoise.backup import restore
from tortoise.sdk import TortoiseSDK, _entity_name_id   # _entity_name_id is MODULE-LEVEL

# NOTE (cycle 8): `_drive` is DEFINED BELOW, in this header, and is NOT
# re-defined anywhere else. Cycles 7 and 8 both found the plan claiming it was
# "hoisted" while leaving the only `def` in Task 8 — so an implementer working
# task-by-task hit `NameError` at Task 3 Step 4, whose Expected says PASS, and
# the plan simultaneously instructed "do NOT define it twice". It is a module
# helper used by tasks 3-8; it lives at the top, once.
def _drive(engine, tmp_path, events, oid, *, require_recovery: bool = True):
    """Run the NAMED engine on a freshly wiped graph. Returns a projection.

    require_recovery=False is for the orphan-retraction case, whose whole point
    is that replay yields ZERO nodes — recover_from_log correctly reports
    recovered=False there, so asserting otherwise is a test bug, not a product
    bug.
    """
    from tortoise.projection import FalkorProjection
    tmp_path.mkdir(parents=True, exist_ok=True)
    proj = FalkorProjection(str(tmp_path / f"{engine}.db"))
    if engine in ("rebuild_all", "recover_from_log", "rebuild"):
        proj.g.query("MATCH (n) DETACH DELETE n")
    if engine == "rebuild_all":
        proj.rebuild_all(str(events))
    elif engine == "recover_from_log":
        res = recover_from_log(str(events), proj)
        if require_recovery:
            assert res["recovered"] is True, res
    elif engine == "rebuild":
        proj.rebuild(EventLog(str(events / "events.jsonl")))
    elif engine == "backup_restore":
        bk = tmp_path / "bk"; bk.mkdir(exist_ok=True)
        # NOTE: the key is "db", not "db_file" — restore() reads
        # manifest.get("db", "tortoise.db") (backup.py:82). A wrong key is
        # silently ignored (the default coincidentally matches), so this
        # fixture would not notice a real manifest regression.
        (bk / "manifest.json").write_text(
            json.dumps({"db": "tortoise.db", "events": 0}))
        shutil.copy(events / "events.jsonl", bk / "events.jsonl")
        restore(str(bk), str(tmp_path / f"{engine}.db"),
                events_path=str(events / "events.jsonl"), into_falkor=True)
    return proj
                                                        # (tortoise/sdk.py:1343; precedent
                                                        # tests/test_object_registered_journal.py:25)
from tortoise.projection.entities import _classify      # must be importable — currently a closure

# NOTE: `json` is needed by Task 2's `_jsonl` and Task 8's backup manifest;
# `shutil` by Task 8's backup-restore fixture. Both were missing from an
# earlier draft, which made those tests raise NameError as written.

DB = "docker://:falkordb@localhost:6379/tortoise_test_matrix"


def test_classify_is_module_level():
    """Regression guard: _classify must not go back to being a nested closure.

    `assert callable(_classify)` was vacuous (true of every function, and the
    header import already fails collection if it is re-nested). Assert the
    structural property instead: a closure's qualname is
    `_fold_object_superseded.<locals>._classify`.
    """
    assert _classify.__qualname__ == "_classify", (
        f"_classify must be module-level; got {_classify.__qualname__!r} "
        "(a re-nested closure yields '_fold_object_superseded.<locals>._classify')")


def test_name_fallback_supersede_after_retraction_is_superseded(tmp_path):
    """D-12 last-wins on the NAME-fallback lane, for the SUPERSEDE family.

    The shared selection helper must not leak the retraction family's
    `<> 'retracted'` filter into the supersede family's name branch. If it
    did, a retraction at seq N followed by a supersession at seq N+1 would
    resolve `retracted` on the name lane but `superseded` on the id lane —
    two branches of one fold disagreeing on the same journal, and a D-12
    violation (last-in-journal-order must win). Verified live in cycle 5.
    """
    sdk = TortoiseSDK(str(tmp_path / "nm-fb.db"))
    proj = sdk._get_proj()
    proj.g.query("MERGE (o:Object {name:'NM'}) SET o.id='ulid-stub', "
                 "o.status='live', o.createdAt='2026-01-01'")
    # 1) retraction by name (id misses) — folds the stub to 'retracted'
    folded, matched = proj._fold_object_retracted(
        {"id": "wrong-id", "name": "NM", "ts": "T1"})
    assert (folded, matched) == (1, 1)
    # 2) supersession by name (id misses) — must WIN (D-12 last-wins)
    folded, matched = proj._fold_object_superseded(
        {"id": "wrong-id-2", "name": "NM", "supersedes_by": "NEW", "ts": "T2"},
        cas=False)
    assert (folded, matched) == (1, 1), \
        "a name-fallback supersede must still fold an already-retracted carrier"
    rows = proj.g.query(
        "MATCH (o:Object {name:'NM'}) RETURN o.status, o.retractedAt").result_set
    assert rows[0][0] == "superseded", \
        "D-12 last-wins: the later supersession must beat the earlier retraction"
    assert rows[0][1] is None, \
        "the supersede fold clears the retraction lane (terminal hygiene)"


def test_classify_cas_loss_is_zero_one(tmp_path):
    """A present-but-terminal node under cas=True must classify (0, 1), not
    (1, 1) — the #2242 atomic guarantee. Without this a CAS loss reads as a
    successful fold. Verified live (docker FalkorDB)."""
    sdk = TortoiseSDK(str(tmp_path / "cas.db"))
    sdk.create_entity("object", "casme", objectKind="core:other", is_episodic=False)
    proj = sdk._get_proj()
    proj.g.query("MATCH (o:Object {name:'casme'}) SET o.status='superseded'")
    folded, matched = proj._fold_object_superseded(
        {"name": "casme", "supersedes_by": "other", "ts": "T2"}, cas=True)
    assert (folded, matched) == (0, 1)
    assert proj.g.query(
        "MATCH (o:Object {name:'casme'}) RETURN o.supersededBy").result_set[0][0] is None


def test_fold_object_retracted_sets_status(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "t1.db"))
    proj = sdk._get_proj()
    sdk.create_entity("object", "fold-me", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "fold-me")
    folded, matched = proj._fold_object_retracted(
        {"id": oid, "name": "fold-me", "ts": "2026-09-11T00:00:00Z"})
    assert (folded, matched) == (1, 1)
    rows = proj.g.query("MATCH (o:Object {id:$id}) RETURN o.status, o.retractedAt",
                        params={"id": oid}).result_set
    assert rows[0][0] == "retracted" and rows[0][1] == "2026-09-11T00:00:00Z"


def test_fold_object_retracted_is_idempotent(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "t2.db"))
    proj = sdk._get_proj()
    sdk.create_entity("object", "fold-twice", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "fold-twice")
    proj._fold_object_retracted({"id": oid, "name": "fold-twice", "ts": "T1"})
    _, matched = proj._fold_object_retracted({"id": oid, "name": "fold-twice", "ts": "T2"})
    assert matched == 1
    rows = proj.g.query("MATCH (o:Object {id:$id}) RETURN o.retractedAt",
                        params={"id": oid}).result_set
    assert rows[0][0] == "T1", "retractedAt must not be overwritten on re-fold"


def test_fold_object_retracted_orphan_returns_zero_zero(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "t3.db"))
    proj = sdk._get_proj()
    assert proj._fold_object_retracted({"id": "obj-nope", "name": "nope", "ts": "T"}) == (0, 0)
    # Replay-only: an orphan retraction must NEVER create an Object. Assert on
    # :Object, NOT on the total node count — TortoiseSDK(...)+_get_proj() leaves
    # a :Meta node behind (verified live: MATCH (n) RETURN count(n) == 1 with
    # labels [["Meta"]]), so a total-count assertion of 0 can never pass.
    assert proj.g.query("MATCH (o:Object) RETURN count(o)").result_set[0][0] == 0
    assert proj.g.query(
        "MATCH (n) WHERE 'Object' IN labels(n) RETURN count(n)"
    ).result_set[0][0] == 0


def test_fold_object_superseded_name_fallback_folds_exactly_one(tmp_path):
    """CYCLE 7 REGRESSION GUARD (Reviewer #5). The shared
    `_fold_object_match_and_apply` extraction is advertised as a pure refactor,
    but it also prepends `WITH o ORDER BY o.createdAt DESC, o.id LIMIT 1` to the
    NAME branch, which CHANGES the existing `ObjectSuperseded` behaviour from
    folding EVERY dup-name row to folding exactly one. Verified live against the
    pre-refactor code: 3 dup-name live Objects + an id-less supersede event →
    `(folded, matched) == (1, 1)` and ALL THREE rows ended `superseded`.

    The change is defensible; the defect was that it was UNPINNED, so it was
    advertised as "identical id-first behaviour" while silently altering a
    production write's amplification (N nodes → 1). This test makes the new
    behaviour explicit, so a regression either way is caught.
    """
    sdk = TortoiseSDK(str(tmp_path / "t1dup.db"))
    proj = sdk._get_proj()
    for i, c in enumerate(("2020-01-01", "2021-01-01", "2022-01-01")):
        proj.g.query("CREATE (:Object {id:$id, name:'dup', status:'live', "
                     "createdAt:$c})",
                     params={"id": f"id-{i}", "c": c})
    folded, matched = proj._fold_object_superseded(
        {"id": "no-such-id", "name": "dup", "ts": "T"})
    assert (folded, matched) == (1, 1), "name fallback folds exactly one carrier"
    rows = proj.g.query(
        "MATCH (o:Object {name:'dup'}) RETURN o.id, o.status "
        "ORDER BY o.createdAt DESC").result_set
    assert rows[0] == ["id-2", "superseded"], "the NEWEST carrier is the one folded"
    assert [r[1] for r in rows[1:]] == ["live", "live"], \
        "the older dup-name carriers are NOT folded (was: all three)"


def test_fold_object_retracted_name_fallback_single_node(tmp_path):
    """A stub-ulid Object (id minted by _event_plain_merge) must still fold by name."""
    sdk = TortoiseSDK(str(tmp_path / "t3b.db"))
    proj = sdk._get_proj()
    proj.g.query("MERGE (o:Object {name:'stub-obj'}) SET o.id='ulid-random', o.status='live'")
    folded, matched = proj._fold_object_retracted(
        {"id": "some-other-id", "name": "stub-obj", "ts": "T"})
    assert (folded, matched) == (1, 1), "id-miss must fall back to the name branch"


def test_fold_object_retracted_clears_supersession_lane(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "t4.db"))
    proj = sdk._get_proj()
    proj.g.query("CREATE (:Object {id:'o1', name:'both', status:'superseded', "
                 "supersededBy:'x', supersededAt:'T0'})")
    proj._fold_object_retracted({"id": "o1", "name": "both", "ts": "T1"})
    rows = proj.g.query("MATCH (o:Object {id:'o1'}) RETURN o.status, o.supersededBy")
    assert rows.result_set[0] == ["retracted", None], \
        "a retracted Object must not carry supersededBy (read status-blind)"


def test_supersede_fold_clears_retraction_lane(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "t5.db"))
    proj = sdk._get_proj()
    proj.g.query("CREATE (:Object {id:'o2', name:'both2', status:'retracted', "
                 "retractedAt:'T0'})")
    proj._fold_object_superseded({"id": "o2", "name": "both2",
                                  "supersedes_by": "y", "ts": "T1"})
    rows = proj.g.query("MATCH (o:Object {id:'o2'}) RETURN o.status, o.retractedAt")
    assert rows.result_set[0] == ["superseded", None], \
        "a superseded Object must not carry retractedAt"


def test_retraction_name_fallback_does_not_fold_duplicate_names(tmp_path):
    """Three nodes share a name (no uniqueness constraint — both `_upsert_object`
    and `_event_plain_merge` MERGE by name behaviourally), one already
    retracted. A retraction whose id matches NONE must fold exactly ONE node,
    and it must be the NEWEST live one. Verified live in cycle 4: without
    `LIMIT 1` every carrier was tombstoned (`matched == 2`+); with a bare
    `LIMIT 1` the pick is scan-order dependent and may select the
    ALREADY-retracted node, reporting a clean `(1, 1)` while the live node
    stayed visible — a false success. The distinct `createdAt` values are what
    make this test discriminate `ORDER BY o.createdAt DESC` from scan order
    (cycle 5: with only one live carrier the ORDER BY was never exercised)."""
    sdk = TortoiseSDK(str(tmp_path / "dup.db"))
    proj = sdk._get_proj()
    proj.g.query("CREATE (:Object {id:'id-a', name:'DUP', status:'retracted', "
                 "retractedAt:'T0', createdAt:'2026-01-01'})")
    proj.g.query("CREATE (:Object {id:'id-b', name:'DUP', status:'live', "
                 "createdAt:'2026-02-01'})")
    proj.g.query("CREATE (:Object {id:'id-c', name:'DUP', status:'live', "
                 "createdAt:'2026-03-01'})")
    folded, matched = proj._fold_object_retracted(
        {"id": "id-OTHER", "name": "DUP", "ts": "T1"})
    assert (folded, matched) == (1, 1), "the fallback must fold exactly one node"
    rows = proj.g.query(
        "MATCH (o:Object {name:'DUP'}) RETURN o.id, o.status ORDER BY o.id"
    ).result_set
    assert rows == [["id-a", "retracted"], ["id-b", "live"], ["id-c", "retracted"]], (
        "exactly the NEWEST live carrier (id-c, createdAt 2026-03-01) must be "
        "retracted; the already-retracted id-a must be skipped (else it is a "
        "clean (1,1) FALSE SUCCESS) and the older live id-b must survive")


def test_fold_object_retracted_skips_null_id_branch(tmp_path):
    """A name-only event must resolve by name directly, not issue a `{id: null}`
    MATCH first (which relies on Cypher null-pattern semantics returning 0 rows).

    CYCLE 6 — the recording wrapper is a GRAPH REPLACEMENT, not a method patch.
    `proj.g` is a `_GuardedGraph` with `__slots__ = ("_g", "_proj")` and a
    CLASS-LEVEL `query`, so `proj.g.query = ...` raises
    `AttributeError: '_GuardedGraph' object attribute 'query' is read-only`
    (verified live) and the test would error at RED. Assigning a new object to
    `proj.g` is fine (verified live).
    """
    sdk = TortoiseSDK(str(tmp_path / "t6.db"))
    proj = sdk._get_proj()
    proj.g.query("MERGE (o:Object {name:'name-only'}) SET o.id='ull', o.status='live'")

    class _RecordingGraph:
        def __init__(self, inner):
            self._inner = inner
            self.calls: list[str] = []
        def query(self, q, **kw):
            self.calls.append(q)
            return self._inner.query(q, **kw)

    rec = _RecordingGraph(proj.g)
    proj.g = rec
    try:
        folded, matched = proj._fold_object_retracted({"name": "name-only", "ts": "T"})
    finally:
        proj.g = rec._inner
    assert (folded, matched) == (1, 1)
    assert not any("{id:$id}" in c for c in rec.calls), \
        "a name-only event must not run the id branch"


def _jsonl(events_dir):
    p = events_dir / "events.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def test_delete_journals_one_retraction(tmp_path):
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t4.db"), event_log_path=str(events / "events.jsonl"))
    sdk.create_entity("object", "del-obj", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "del-obj")
    assert sdk._delete_entity(oid) is True
    retr = [l for l in _jsonl(events) if l.get("type") == "ObjectRetracted"]
    assert len(retr) == 1 and retr[0]["id"] == oid and retr[0]["name"] == "del-obj"


def test_double_delete_emits_once(tmp_path):
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t4b.db"), event_log_path=str(events / "events.jsonl"))
    sdk.create_entity("object", "twice", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "twice")
    assert sdk._delete_entity(oid) is True
    assert sdk._delete_entity(oid) is False, "second delete must report no rows"
    assert len([l for l in _jsonl(events) if l.get("type") == "ObjectRetracted"]) == 1


def test_delete_of_absent_id_emits_nothing(tmp_path):
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t5.db"), event_log_path=str(events / "events.jsonl"))
    assert sdk._delete_entity("obj-nope") is False
    assert [l for l in _jsonl(events) if l.get("type") == "ObjectRetracted"] == []


def test_delete_of_point_warns_about_non_durability(tmp_path, caplog):
    """D-9's other half, which the plan states as an acceptance criterion and
    previously never asserted: a non-Object delete with a journal configured must
    WARN (the contract is now label-inconsistent), and must NOT warn when no
    journal is configured (nothing is being lost)."""
    events = tmp_path / "events"; events.mkdir(exist_ok=True)
    sdk = TortoiseSDK(str(tmp_path / "t6e.db"), event_log_path=str(events / "events.jsonl"))
    pid = sdk.create_point("statement", "pw")["id"]
    with caplog.at_level("WARNING"):
        assert sdk._delete_entity(pid) is True
    assert any("NOT durable across rebuild" in r.getMessage() for r in caplog.records), \
        "a non-Object delete with a journal must warn (D-9)"
    caplog.clear()
    sdk2 = TortoiseSDK(str(tmp_path / "t6f.db"))          # no event_log_path
    pid2 = sdk2.create_point("statement", "pw2")["id"]
    with caplog.at_level("WARNING"):
        assert sdk2._delete_entity(pid2) is True
    assert not [r for r in caplog.records if "NOT durable" in r.getMessage()], \
        "no journal configured => nothing lost => no warning"


def test_delete_of_point_does_not_emit_object_retracted(tmp_path):
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t6.db"), event_log_path=str(events / "events.jsonl"))
    # create_point(...) returns an OrderedDict (verified live), NOT an id string —
    # passing the dict makes _delete_entity return False without deleting.
    pid = sdk.create_point("statement", "p")["id"]
    assert sdk._delete_entity(pid) is True
    assert [l for l in _jsonl(events) if l.get("type") == "ObjectRetracted"] == []


def test_delete_of_object_emits_zero_non_durability_warnings(tmp_path, caplog):
    """VERIFY-1 P1-2 (Reviewer slot 1, EMPIRICAL) — the EXACT-COUNT pin for
    D-9's warning gate that the plan claimed to have but did not.

    Cycle 6 found the gate written `if not n and label != "Object"`, which fired
    on every arm that matched NOTHING — 5 spurious "NOT durable" warnings on an
    Object delete. The gate was corrected to `if n and label != "Object"`.

    Without an exact-count assertion the INVERTED form is invisible to the whole
    suite: slot 1 flipped `if n and` back to `if not n and` and every Task-2 test
    still passed (`5 passed`). These assertions are what make the gate
    regression-visible; the sibling test above pins the POSITIVE (Point warns)
    and the no-journal negative, neither of which catches the inversion.
    """
    events = tmp_path / "events"; events.mkdir(exist_ok=True)
    sdk = TortoiseSDK(str(tmp_path / "t6e2.db"), event_log_path=str(events / "events.jsonl"))
    oid = sdk.create_object("wd-object", objectKind="core:other")["id"]
    with caplog.at_level("WARNING"):
        assert sdk._delete_entity(oid) is True
    assert [r for r in caplog.records if "NOT durable" in r.getMessage()] == [], \
        ("an OBJECT delete with a journal must emit ZERO non-durability warnings "
         "— the Object lane IS journaled (D-9). A non-zero count means the gate "
         "regressed to the cycle-6 `if not n` form, which fires on empty matches.")
    caplog.clear()
    # The exact-count positive: a Point delete warns exactly ONCE, not 1+N.
    pid = sdk.create_point("statement", "wd-point")["id"]
    with caplog.at_level("WARNING"):
        assert sdk._delete_entity(pid) is True
    assert len([r for r in caplog.records if "NOT durable" in r.getMessage()]) == 1, \
        "a Point delete warns exactly once (D-9); >1 means the empty-match arm fired"


def test_update_entity_object_guard_holds_for_point_object_multi_label(tmp_path):
    """EMPIRICAL (cycle 4): `labels(n)[0]` returns 'Point' for a :Point:Object
    node, so a probe using [0] resolves to Point and BYPASSES the Object guard.
    Use `'Object' IN labels(n)`.

    CYCLE 5 — POINT-LABEL PRECEDENCE. A :Point:Object node is governed by the
    POINT vocabulary, because POINT_STATUS_VALUES allows `draft`/`outdated`
    (both ABSENT from OBJECT_STATUS_VALUES) and the Point writers write the
    same physical property. Without the `NOT 'Point' IN labels(n)` conjunct,
    `update_entity(<point-object id>, status='draft')` — a working public call
    (verified live in cycle 5) — would raise AFTER the Point branch had already
    written. The Object-ONLY guard is asserted below; the multi-label
    Object-status gap is UNCHANGED from today and is filed as follow-up (m).
    """
    sdk = TortoiseSDK(str(tmp_path / "t6g.db"))
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (:Point:Object {id:'ml1', name:'ML1', status:'live'})")
    # Point vocabulary governs a :Point:Object node — neither may raise.
    sdk.update_entity("ml1", status="draft")
    sdk.update_entity("ml1", status="outdated")
    assert proj.g.query(
        "MATCH (n {id:'ml1'}) RETURN n.status").result_set[0][0] == "outdated"
    # An Object-ONLY node still gets the full guard.
    sdk.create_entity("object", "obj-only", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "obj-only")
    with pytest.raises(ValueError):
        sdk.update_entity(oid, status="retracted")
    with pytest.raises(ValueError):
        sdk.update_entity(oid, status="nonsense")


def test_delete_point_object_multilabel_emits_one_retraction(tmp_path):
    """CYCLE 5: `:Point` is the FIRST arm of _delete_entity's loop, so for a
    :Point:Object node the Point arm deletes it and the Object arm returns 0
    rows. Keying the emission on the Object arm therefore emits NOTHING.
    Verified live: the journal was empty after `_delete_entity('ml1')` returned
    True. The emission is now keyed on a LABEL probe taken before any delete.

    **CYCLE 8 — WHAT THIS TEST DOES *NOT* ESTABLISH, and why the claim shrank.**
    An earlier draft said the emitted line stops the resurrection. It does not.
    `:Object` is added to a Point by raw Cypher (`SET n:Object` — the ONLY
    source; no production path mints a :Point:Object, and
    `tests/test_write_consolidation.py:156` is a test), that label write is
    NEVER journaled, and `_upsert_point_props` does not re-apply labels — so
    replay reconstructs the node as `:Point`-ONLY. Both fold branches
    (`MATCH (o:Object {id:$id})` / `(o:Object {name:$name})`) therefore match
    NOTHING and the retraction is a silent orphan. **Verified live in cycle 8:**
    live labels `['Point','Object']` -> replay labels `['Point']`, replay
    `:Object` count 0. So a `:Point:Object` delete is NOT made durable by
    #2977; the emitted line is journal noise on that shape.

    This test asserts ONLY the emission (it counts journal lines). It is
    green on a graph that still resurrects, which is exactly why the limitation is
    written down here and pinned separately by
    `test_multilabel_point_object_delete_is_still_not_durable` below. Filed as
    follow-up (r).
    """
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t6i.db"),
                      event_log_path=str(events / "events.jsonl"))
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (:Point:Object {id:'ml2', name:'ML2', status:'live'})")
    assert sdk._delete_entity("ml2") is True
    rets = [l for l in _jsonl(events) if l.get("type") == "ObjectRetracted"]
    assert len(rets) == 1, (
        f"a :Point:Object delete must emit exactly ONE ObjectRetracted "
        f"(got {len(rets)}) — the Point arm runs first, so an Object-arm-keyed "
        "emission never fires")
    assert rets[0]["id"] == "ml2" and rets[0].get("name") == "ML2"


def test_multilabel_point_object_delete_is_still_not_durable(tmp_path):
    """CYCLE 8 REGRESSION GUARD (Reviewer #4, EMPIRICAL) — pins a LIMITATION,
    not a fix. Same fixture as the test above, but asserts the REPLAY OUTCOME
    rather than the emission, which is what makes the gap visible.

    A `:Point:Object` node: (1) gets its `:Object` label from a raw `SET n:Object`
    that is never journaled; (2) replays through `PointAdded` as a `:Point`-only
    node, because `_upsert_point_props` does not re-apply labels. The
    `ObjectRetracted` line IS emitted, but BOTH fold branches match on
    `(o:Object …)`, so neither matches and the fold is a silent orphan — the
    retraction is not durable on this shape.

    NOTE the trap this also documents: D-11's headline acceptance indicator
    ("0 Objects with `status='live'`") is VACUOUSLY satisfied here — there are
    zero `:Object` nodes at all — while a live `:Point` is served by every read
    surface. An acceptance check written only against `:Object` cannot see this
    class. Filed as follow-up (r); fix directions: emit the Point-side terminal
    event too, fold by `id` across labels, or journal the label add.
    """
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t6i2.db"),
                      event_log_path=str(events / "events.jsonl"))
    proj = sdk._get_proj()
    # CYCLE 10 (Reviewer #4, EMPIRICAL): the fixture MUST pass status="live".
    # `create_point` defaults to `status="draft"` (sdk.py:2455, #131), so the
    # cycle-9 draft of this pin asserted `[["live"]]` against a Point that
    # replays as `draft` — an unsatisfiable pin whose own failure message told
    # the implementer to DELETE the pin and close (r), in the dangerous
    # direction. Verified live: with status="live" the replay is `[["live"]]`,
    # and it still flips to `retracted`/absent under all three (r) fix
    # directions, so the pin remains falsifiable in the right direction.
    p = sdk.create_point("observation", "multilabel claim", status="live")
    pid = p["id"]
    proj.g.query("MATCH (n:Point {id:$id}) SET n:Object", params={"id": pid})
    assert ["Object"] == [l for l in proj.g.query(
        "MATCH (n {id:$id}) RETURN labels(n)", params={"id": pid}
    ).result_set[0][0] if l == "Object"], "precondition: :Object is present live"
    assert sdk._delete_entity(pid) is True
    proj.rebuild_all(str(events))
    rows = proj.g.query("MATCH (o:Object) RETURN count(o)").result_set
    assert rows[0][0] == 0, "no :Object survives — the label was never journaled"
    live_point = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status", params={"id": pid}
    ).result_set
    # CYCLE 9 (Reviewers #2 and #4): this must assert the STATUS, not merely
    # that a row exists. `assert live_point` (an earlier draft) is true whether
    # the replayed Point is `live` OR `retracted`, so it stays GREEN under two
    # of the three fix directions (fold-by-id-across-labels; emit the Point-side
    # terminal event) and would be left stale while (r) is closed — the "green
    # blind test" this plan condemns elsewhere. Verified: only the
    # journal-the-label-add fix flips a truthiness assertion.
    # CYCLE 10: the failure DIRECTION matters. This pin fails when the shape
    # changes, and the only value that means a fix landed is `retracted`/no row.
    assert live_point == [["live"]], (
        "PINNED LIMITATION (r): the deleted claim is served again as a `live` "
        ":Point — the ObjectRetracted line could not fold because replay drops "
        "the :Object label. CYCLE 10: read the observed value before acting. "
        "`[['draft']]` means the FIXTURE is wrong (it must pass "
        "status=\"live\"); only `retracted` or an EMPTY row means one of the "
        "three (r) fix directions landed, in which case invert this pin to "
        "expect the retracted status (or no row) and close (r).")


def test_create_object_rejects_retracted_and_unknown(tmp_path):
    """EMPIRICAL (cycle 4): the CREATE funnel accepts status via **props and
    journals it — a DURABLE bad tombstone. Guarding only _update_entity leaves
    this open."""
    sdk = TortoiseSDK(str(tmp_path / "t6h.db"))
    with pytest.raises(ValueError):
        sdk.create_object("co-retracted", objectKind="core:other", status="retracted")
    with pytest.raises(ValueError):
        sdk.create_entity("object", "co-bogus", objectKind="core:other", status="bogus")
    assert sdk._get_proj().g.query(
        "MATCH (o:Object) RETURN count(o)").result_set[0][0] == 0, \
        "a rejected create must leave no node behind"


def _post_objects(client, team):
    for bad in ("retracted", "bogus"):
        r = client.post("/v1/objects", json={
            "name": f"api-bad-{bad}", "objectKind": "core:other", "status": bad})
        assert r.status_code == 422, (
            f"status={bad!r} must be a 422 client error, got {r.status_code} "
            f"({r.text[:200]}) — a 500 here means the guard was raised INSIDE the "
            "handler's blanket `except Exception` and got converted")
        assert r.status_code != 500


def test_hosted_api_create_object_rejects_unknown_status_with_422(tmp_path):
    """VERIFY-1 P1-3 (Reviewer slot 1): Task 5's Step 4 names "the 422 test" as
    required output, but no such test existed anywhere in the plan — the new
    `HTTPException(422)` branch and its placement were entirely unverified.

    `fastapi.HTTPException` SUBCLASSES `Exception`, and the handler wraps
    `sdk.create_object` in `except Exception: raise HTTPException(500, ...)`. So
    a 422 raised INSIDE that `try` is caught and re-reported as a 500 — the
    exact "client error reported as a server fault" defect this change removes.
    This test fails loudly in that case: it asserts 422 AND asserts not 500.

    VERIFY-2 P0-2 (slot 1, EMPIRICAL): a bare `TestClient(app)` CANNOT reach the
    handler. `/v1/objects` is `Depends(get_current_team_session_ungated)`, so an
    unauthenticated POST returns `401 {"detail":"Missing session token"}` — the
    test never exercised the guard. It also needs a team-limits dict, because
    the guard sits AFTER `_check_team_limit(team, "points")`, which 500s on a
    stub team (`Quota check failed: team limits missing max_points`). So this
    test must install the SAME override the repo's own route tests use —
    `app.dependency_overrides[get_current_team]` plus a populated `TEST_TEAM`
    limits dict — exactly as `tests/test_hosted_api.py` does.
    """
    from fastapi.testclient import TestClient
    from tortoise.hosted_api import app, get_current_team
    from tests.test_hosted_api import TEST_TEAM

    app.dependency_overrides[get_current_team] = lambda: TEST_TEAM
    try:
        client = TestClient(app)
        _post_objects(client, TEST_TEAM)
    finally:
        app.dependency_overrides.pop(get_current_team, None)




def test_update_entity_point_status_vocab_unaffected(tmp_path):
    """The Object vocabulary guard must not fire for Points.
    POINT_STATUS_VALUES (sdk.py:266) has 'draft' and 'outdated', which are absent
    from OBJECT_STATUS_VALUES — a guard keyed on the six-label loop variable
    would reject a currently-working public call (verified live)."""
    sdk = TortoiseSDK(str(tmp_path / "t6c.db"))
    pid = sdk.create_point("statement", "pd")["id"]
    for st in ("draft", "outdated"):
        sdk.update_entity(pid, status=st)          # must not raise


def test_update_entity_rejects_retracted_and_unknown(tmp_path):
    """The A8 loophole: `_update_entity` could write status='retracted' with no
    journal line and no retractedAt. Reject it (fail-closed), plus unknowns."""
    sdk = TortoiseSDK(str(tmp_path / "t6d.db"))
    sdk.create_entity("object", "guarded", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "guarded")
    with pytest.raises(ValueError):
        sdk.update_entity(oid, status="bogus")
    with pytest.raises(ValueError):
        sdk.update_entity(oid, status="retracted")
    # No partial write: the node is untouched by either rejected call.
    assert sdk._get_proj().g.query(
        "MATCH (o:Object {id:$id}) RETURN o.status", params={"id": oid}
    ).result_set[0][0] == "live"


def test_retraction_append_failure_warns(tmp_path, monkeypatch, caplog):
    """Pins the partial-failure contract: graph delete succeeds, journal append
    fails. _emit_event swallows the raise (sdk.py:2340-2352), so the delete still
    returns True and the Object is live-deleted but NOT durable."""
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t6b.db"), event_log_path=str(events / "events.jsonl"))
    sdk.create_entity("object", "lost", objectKind="core:other", is_episodic=False)
    from tortoise.log import EventLog
    monkeypatch.setattr(EventLog, "append", lambda self, e: (_ for _ in ()).throw(OSError("disk")))
    with caplog.at_level("WARNING"):
        assert sdk._delete_entity(_entity_name_id("Object", "lost")) is True
    # Assert the SPECIFIC message, not `"append" or "event"` — that `or` is
    # satisfied by nearly every warning this logging surface emits, so it never
    # pins the swallowed-append case it names.
    msgs = [r.getMessage() for r in caplog.records]
    assert any("ObjectRetracted" in m and "append" in m.lower() for m in msgs), \
        ("a swallowed journal-append failure must be logged with a message that "
         "names the cause; got: %r" % (msgs,))


def test_deleted_object_not_resurrected_on_rebuild(tmp_path):
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t7.db"), event_log_path=str(events / "events.jsonl"))
    proj = sdk._get_proj()
    sdk.create_entity("object", "gone", objectKind="core:other", is_episodic=False)
    assert sdk._delete_entity(_entity_name_id("Object", "gone")) is True
    proj.rebuild_all(str(events))
    rows = proj.g.query("MATCH (o:Object {name:'gone'}) RETURN o.status").result_set
    # EXACT, not `(not rows) or rows[0][0] == "retracted"`: accepting both states
    # makes the assertion blind to the very difference it exists to pin
    # (Surface Map row 14, #688 D7's "parity test blind to its subject").
    assert rows == [["retracted"]], (
        "Object removal must replay as a TOMBSTONE, never as absence and never "
        "as live — the tombstone is what keeps a later re-creation coherent")


def test_live_delete_and_replayed_tombstone_diverge_by_design(tmp_path):
    """D-5's PIN. An earlier draft ended `assert inbound >= 0` — a TAUTOLOGY
    (a COUNT is never negative) over an edge that was never journaled, so it
    could not fail and did not observe the divergence it claimed to make
    'audible'. This version asserts BOTH sides concretely.

    CYCLE 5 — THE EDGE MUST COME FROM THE PRODUCTION LANE. The earlier draft
    built the edge with raw Cypher between two **Objects**; nothing in
    `tortoise/` generates an Object→Object `aboutObject` edge, so the green
    `count == 0` was not evidence about the declared divergence (a #688 D8
    "measured lane ≠ production lane" defect). The real lane is Point→Object,
    reconstructed on replay from the Point's journaled `aboutEntities`
    (pass-2 → `_upsert_point_edges` → `_create_about_edges`,
    `projection/edges.py:257`).

    CYCLE 6 — AND THE LIVE SIDE MUST BE WIRED, OR IT IS VACUOUS. Driving
    `create_point(aboutEntities=[...])` alone does NOT create a live
    `aboutObject` edge — verified live (0 edges), and that is open issue
    **#2501** ("`create_point(aboutEntities=...)` never wires about edges live,
    but rebuild derives them from the journaled PointAdded snapshot"). With that
    fixture the live `count == 0` holds BEFORE the delete too, so it could not
    fail and the `_delete_entity` call was decorative. The live edge is now
    materialized through the production writer (`proj._create_about_edges`,
    the same call `sdk.py:8541` makes), asserted present BEFORE the delete and
    absent after — verified live: 0 (create_point alone) → 1 (writer) → 0
    (delete).

    VERIFIED LIVE: live → 0 Objects, 0 `aboutObject` edges; replay → 1 Object
    and 1 `aboutObject` edge, reconstructed from the Point snapshot.
    """
    events = tmp_path / "events"; events.mkdir(exist_ok=True)
    sdk = TortoiseSDK(str(tmp_path / "t7b.db"), event_log_path=str(events / "events.jsonl"))
    proj = sdk._get_proj()
    sdk.create_entity("object", "EDGEY", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "EDGEY")
    # The PRODUCTION lane: the Point's journaled aboutEntities names the Object,
    # AND the live edge is wired by the production writer (#2501 means
    # create_point alone does not do it).
    pid = sdk.create_point("statement", "a point about EDGEY",
                           aboutEntities=["EDGEY"])["id"]
    proj._create_about_edges(pid, "EDGEY")
    assert proj.g.query(
        "MATCH ()-[r:aboutObject]->() RETURN count(r)").result_set[0][0] == 1, (
        "the live edge must EXIST before the delete, or the live == 0 "
        "assertion below is vacuous (cycle 6)")

    sdk._delete_entity(oid)

    # ── LIVE: node gone, so the edge that pointed at it is gone with it. ──
    assert proj.g.query(
        "MATCH (o:Object {id:$id}) RETURN count(o)", params={"id": oid}
    ).result_set[0][0] == 0
    assert proj.g.query(
        "MATCH ()-[r:aboutObject]->() RETURN count(r)").result_set[0][0] == 0

    # ── REPLAY: the node is back as a TOMBSTONE, and pass-2 reconstructs the
    # Point→Object edge from the Point's own snapshot — the divergence D-5
    # records. If the fold ever starts deleting recorded edges, this fires.
    proj.rebuild_all(str(events))
    assert proj.g.query(
        "MATCH (o:Object {id:$id}) RETURN o.status", params={"id": oid}
    ).result_set[0][0] == "retracted"
    assert proj.g.query(
        "MATCH (p:Point)-[r:aboutObject]->(o:Object {id:$id}) RETURN count(r)",
        params={"id": oid}).result_set[0][0] == 1, (
        "replay KEEPS the recorded edge that live dropped — the accepted D-5 "
        "divergence; preserving it is what keeps the tombstone auditable")
    assert proj.g.query("MATCH (o:Object) RETURN count(o)").result_set[0][0] == 1


def test_anchor_ignores_a_registration_that_created_no_node(tmp_path):
    """CYCLE 7 REGRESSION GUARD (Reviewer #4, EMPIRICAL). `_upsert_object`
    early-returns WITHOUT raising when `not oid or not name`
    (projection/entities.py:487-489), so an empty-name `ObjectRegistered` still
    reached `_recreate`. Verified live on the pre-fix code:

        journal [OR(U1,'X'), RT(U1,'X'), OR(U1,'')]
        -> `_last` = 2, the seq-1 retraction DROPPED, node `['U1','X','live']`

    i.e. a de-registration that created nothing extended the anchor window and
    buried a legitimate retraction — the exact #2977 failure direction. The
    anchor now requires BOTH keys truthy (mirroring `_upsert_object`'s guard).
    """
    events = tmp_path / "events"; events.mkdir(exist_ok=True)
    log = EventLog(str(events / "events.jsonl"))
    log.append({"type": "ObjectRegistered", "id": "U1", "name": "X"})
    log.append({"type": "ObjectRetracted", "id": "U1", "name": "X", "ts": "T1"})
    log.append({"type": "ObjectRegistered", "id": "U1", "name": ""})   # created NOTHING
    proj = _drive("rebuild_all", tmp_path, events, "U1")
    try:
        assert proj.g.query(
            "MATCH (o:Object {id:'U1'}) RETURN o.status").result_set[0][0] \
            == "retracted", \
            "a node-less registration must not drop the retraction that follows it"
    finally:
        proj.close()


def test_survivor_anchor_cross_key_disagreement_is_documented(tmp_path):
    """PINS an acknowledged ambiguity in the `max(by_id, by_name)` anchor
    (cycle 5, Reviewer #4). When a fold's `id` and `name` resolve to DIFFERENT
    registrations, the max across keys can drop a fold whose id-target was
    never re-created:

        OR(iA, NA)@0,  RT(iA, NB)@1,  OR(iB, NB)@2
        -> max(by_id['iA']=0, by_name['NB']=2) = 2  =>  the seq-1 fold is DROPPED
           and iA/NA stays `live`, though its own id was never re-created.

    The `max` is NOT wrong for the case it was introduced for —
    `OR(U1,X) -> Retract(U1,X) -> OR(U2,X)`, where an ID-FIRST lookup finds the
    stale anchor, the fold is wrongly kept, and its name fallback then stamps
    the re-created LIVE node `retracted` (verified live, cycle 3). Both shapes
    route through the same two lines; changing one flips the other, so the
    behaviour is PINNED rather than silently adjusted.

    Reachability is low: production never emits a retraction whose id and name
    come from different nodes — `_delete_entity` reads both from ONE
    `MATCH (o:Object {id:$id}) RETURN o.name`. A hand-written or legacy journal
    can produce it. Filed alongside follow-up (g) as the family's known edge.
    """
    events = tmp_path / "events"; events.mkdir(exist_ok=True)
    log = EventLog(str(events / "events.jsonl"))
    # OR(iA, NA)@0 — an Object whose NAME is NA
    log.append({"type": "ObjectRegistered", "id": "iA", "name": "NA"})
    # RT(iA, NB)@1 — a retraction naming the SAME id under a DIFFERENT name
    log.append({"type": "ObjectRetracted", "id": "iA", "name": "NB", "ts": "T1"})
    # OR(iB, NB)@2 — a LATER registration of the name NB under a different id
    log.append({"type": "ObjectRegistered", "id": "iB", "name": "NB"})
    proj = _drive("rebuild_all", tmp_path, events, "iA")
    try:
        # PINNED CURRENT BEHAVIOUR: the cross-key max drops the seq-1 fold, so
        # iA stays live. If this assertion starts failing, the anchor rule
        # changed — decide deliberately and update Surface Map row 6 + (g).
        assert proj.g.query(
            "MATCH (o:Object {id:'iA'}) RETURN o.status"
        ).result_set[0][0] == "live", (
            "the cross-key max drops the seq-1 fold (documented ambiguity) — "
            "a change here must be a deliberate decision, not a side effect")
    finally:
        proj.close()


def test_delete_recreate_replays_live(tmp_path):
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t8.db"), event_log_path=str(events / "events.jsonl"))
    proj = sdk._get_proj()
    sdk.create_entity("object", "phoenix", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "phoenix")
    sdk._delete_entity(oid)
    sdk.create_entity("object", "phoenix", objectKind="core:other", is_episodic=False)
    proj.rebuild_all(str(events))
    rows = proj.g.query("MATCH (o:Object {id:$id}) RETURN o.status, o.createdAt",
                        params={"id": oid}).result_set
    assert rows and rows[0][0] == "live", (
        "a pre-recreation retraction must be dropped (survivor rule) — otherwise "
        "the re-created Object is permanently buried")


def test_delete_recreate_replays_live_via_recover_from_log(tmp_path):
    """Exercise the PRODUCTION lane, not apply_replay directly."""
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t9.db"), event_log_path=str(events / "events.jsonl"))
    proj = sdk._get_proj()
    sdk.create_entity("object", "phoenix2", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "phoenix2")
    sdk._delete_entity(oid)
    sdk.create_entity("object", "phoenix2", objectKind="core:other", is_episodic=False)
    # Wipe ALL nodes, not just :Object: recover_from_log short-circuits when
    # `_node_count() > 0`, and create_entity leaves a :Meta node behind, so an
    # Object-only wipe returns {"recovered": False, "reason": "graph already has
    # nodes — no rebuild"} and the test never reaches the replay it tests.
    proj.g.query("MATCH (n) DETACH DELETE n")
    from tortoise.consistency import recover_from_log
    # REAL signature: recover_from_log(events_dir: str, projection) -> dict
    # (consistency.py:36). On SUCCESS `reason` is a descriptive string
    # (f"replayed {applied} events from {files[0]}", :128-132) — NEVER None, so
    # `assert res["reason"] is None` can never pass. Assert the real contract:
    res = recover_from_log(str(events), proj)
    assert res["recovered"] is True
    assert res["reason"].startswith("replayed")
    rows = proj.g.query("MATCH (o:Object {id:$id}) RETURN o.status", params={"id": oid}).result_set
    assert rows and rows[0][0] == "live"


def test_rebuild_retracts_deleted_object(tmp_path):
    """D-14: `rebuild(log)` is a real replay surface (**11** in-repo callers — see
    D-14; VERIFY-2 P2-1 corrected this stale `10`) and
    MUST NOT resurrect. Verified live pre-fix: rebuild(log) → status='live'."""
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t9b.db"), event_log_path=str(events / "events.jsonl"))
    proj = sdk._get_proj()
    sdk.create_entity("object", "gone", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "gone")
    sdk._delete_entity(oid)
    assert proj.g.query(
        "MATCH (o:Object {id:$id}) RETURN count(o)", params={"id": oid}
    ).result_set[0][0] == 0
    from tortoise.log import EventLog
    proj.rebuild(EventLog(str(events / "events.jsonl")))
    rows = proj.g.query("MATCH (o:Object {id:$id}) RETURN o.status",
                        params={"id": oid}).result_set
    assert rows and rows[0][0] == "retracted", \
        "rebuild(log) must not resurrect a deleted Object"


def test_rebuild_stays_fail_loud(tmp_path, monkeypatch):
    """D-14: routing rebuild() through apply_replay must NOT flip its contract.
    `strict=True` re-raises where recover_from_log would swallow.
    Asserts the POST-STATE too: a bare `pytest.raises` is satisfied by an
    exception while the graph is left silently wrong (verified live: the node
    remains `status='live'` with the retraction fold lost)."""
    events = tmp_path / "events"; events.mkdir(exist_ok=True)
    sdk = TortoiseSDK(str(tmp_path / "t9c.db"), event_log_path=str(events / "events.jsonl"))
    proj = sdk._get_proj()
    sdk.create_entity("object", "boom", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "boom")
    sdk._delete_entity(oid)

    def _boom(_ev):
        raise RuntimeError("injected fold failure")

    monkeypatch.setattr(proj, "_fold_object_retracted", _boom)
    from tortoise.log import EventLog
    with pytest.raises(RuntimeError):
        proj.rebuild(EventLog(str(events / "events.jsonl")))
    # Documented consequence: the graph is HALF-FOLDED and `rebuild()` returns
    # None, so the caller cannot tell how far it got. Recorded, not tolerated
    # silently — a second, successful rebuild is required to fix it.
    assert proj.g.query(
        "MATCH (o:Object {id:$id}) RETURN o.status", params={"id": oid}
    ).result_set[0][0] == "live", \
        "half-folded post-state: pin it so a future reordering of the raise is noticed"


def test_apply_replay_fold_failure_is_isolated(tmp_path, monkeypatch):
    """Non-strict: one bad fold must not abort the rest, and must not escape
    recover_from_log's documented "caught and reported, never raised" contract.
    Verified live pre-fix: a raise on fold #1 of 2 left BOTH unfolded."""
    events = tmp_path / "events"; events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "t9d.db"), event_log_path=str(events / "events.jsonl"))
    proj = sdk._get_proj()
    for nm in ("f1", "f2", "f3"):
        sdk.create_entity("object", nm, objectKind="core:other", is_episodic=False)
        sdk._delete_entity(_entity_name_id("Object", nm))
    _orig, calls = proj._fold_object_retracted, {"n": 0}

    def _flaky(ev):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("transient")
        return _orig(ev)

    monkeypatch.setattr(proj, "_fold_object_retracted", _flaky)
    proj.g.query("MATCH (n) DETACH DELETE n")
    _, _, torn = proj.apply_replay([
        {"type": "ObjectRegistered", "id": "o1", "name": "f1"},
        {"type": "ObjectRetracted", "id": "o1", "name": "f1"},
        {"type": "ObjectRegistered", "id": "o2", "name": "f2"},
        {"type": "ObjectRetracted", "id": "o2", "name": "f2"},
        {"type": "ObjectRegistered", "id": "o3", "name": "f3"},
        {"type": "ObjectRetracted", "id": "o3", "name": "f3"},
    ])
    assert torn >= 1, "a failed fold must be counted, not dropped"
    assert proj.g.query(
        "MATCH (o:Object) WHERE o.status='retracted' RETURN count(o)"
    ).result_set[0][0] >= 2, "the folds after the failure must still run"


def test_backup_restore_raises_on_a_torn_fold(tmp_path, monkeypatch):
    """CYCLE 8 REGRESSION GUARD. `restore()` is fail-LOUD: its pre-change loop
    (`for ev: proj.apply(ev)` in try/finally) propagated a raising `apply()`,
    and routing through `apply_replay(strict=True)` preserves that. An earlier
    draft instead asserted a returned `{"torn": n, "status": "torn"}` — but
    `strict=True` RE-RAISES inside the flush, so `fold_torn` never becomes
    non-zero on a returning path and the exception escapes `restore()` (which
    has no `except`) BEFORE the assertions. The test therefore errored, and the
    `torn` surface it pinned was dead code. This asserts the real contract.

    (Cycle 4 found the original defect: `restore()` reported `status: ok` no
    matter which folds failed. Under fail-loud that is no longer possible —
    a torn fold cannot be reported as ok because it cannot be reported at all.)
    """
    events = tmp_path / "events"; events.mkdir(exist_ok=True)
    sdk = TortoiseSDK(str(tmp_path / "rb.db"), event_log_path=str(events / "events.jsonl"))
    oid = _entity_name_id("Object", "rb")
    sdk.create_entity("object", "rb", objectKind="core:other", is_episodic=False)
    sdk._delete_entity(oid)
    bk = tmp_path / "bk"; bk.mkdir()
    # manifest key is "db" (backup.py:82 reads manifest.get("db", ...))
    (bk / "manifest.json").write_text(
        json.dumps({"db": "tortoise.db", "events": 0}))
    shutil.copy(events / "events.jsonl", bk / "events.jsonl")

    from tortoise.projection import FalkorProjection
    monkeypatch.setattr(FalkorProjection, "_fold_object_retracted",
                        lambda self, ev: (_ for _ in ()).throw(RuntimeError("transient")))
    with pytest.raises(RuntimeError, match="transient"):
        restore(str(bk), str(tmp_path / "rb2.db"),
                events_path=str(events / "events.jsonl"), into_falkor=True)


def test_recover_from_log_torn_retraction_is_not_reported_recovered(tmp_path, monkeypatch):
    """EMPIRICAL (cycle 4): a torn retraction fold left the Object `live` while
    recover_from_log returned `{'recovered': True, 'reason': 'replayed 1 events
    ... (1 skipped)'}` — a RESURRECTED Object reported as successful recovery,
    contradicting the issue's own acceptance indicator. `ok` must require
    torn == 0."""
    events = tmp_path / "events"; events.mkdir(exist_ok=True)
    sdk = TortoiseSDK(str(tmp_path / "rc.db"), event_log_path=str(events / "events.jsonl"))
    oid = _entity_name_id("Object", "rc")
    sdk.create_entity("object", "rc", objectKind="core:other", is_episodic=False)
    sdk._delete_entity(oid)

    from tortoise.projection import FalkorProjection
    monkeypatch.setattr(FalkorProjection, "_fold_object_retracted",
                        lambda self, ev: (_ for _ in ()).throw(RuntimeError("transient")))
    proj = FalkorProjection(str(tmp_path / "rc2.db"))
    proj.g.query("MATCH (n) DETACH DELETE n")
    try:
        res = recover_from_log(str(events), proj)
        assert res["recovered"] is False, \
            "recovery must not claim success on a graph where the fix did not apply"
    finally:
        proj.close()


def test_recover_from_log_tolerates_a_torn_trailing_line(tmp_path):
    """CYCLE 5 REGRESSION GUARD. The parse-time tear counter and the replay tear
    counter are SEPARATE. A journal whose LAST line was truncated mid-append is
    the canonical crash artifact this function exists to serve — its own
    docstring says such lines 'are skipped, not fatal'. Folding the parse count
    into `ok` (the earlier `torn == 0` form) made `recovered` False here, and
    `FalkorProjection._recover_or_raise` (projection/__init__.py:985) then RAISES
    — an embedded DB damaged only in its final byte refuses to open.
    """
    events = tmp_path / "events"; events.mkdir(exist_ok=True)
    sdk = TortoiseSDK(str(tmp_path / "pt.db"),
                      event_log_path=str(events / "events.jsonl"))
    sdk.create_entity("object", "pt", objectKind="core:other", is_episodic=False)
    # Truncate the tail mid-append, exactly as a crash would leave it.
    p = events / "events.jsonl"
    p.write_text(p.read_text() + '{"type": "ObjectRegistered", "id": "ob')

    from tortoise.projection import FalkorProjection
    proj = FalkorProjection(str(tmp_path / "pt2.db"))
    proj.g.query("MATCH (n) DETACH DELETE n")
    try:
        res = recover_from_log(str(events), proj)
        assert res["recovered"] is True, \
            f"a torn TRAILING line is tolerated, not fatal: {res}"
        assert "skipped" in res["reason"], \
            "the parse tear must still be REPORTED in the reason string"
    finally:
        proj.close()


def test_recover_from_log_tolerates_a_per_event_apply_failure(tmp_path):
    """CYCLE 6 REGRESSION GUARD (Reviewer #4, EMPIRICAL). `apply_replay`'s
    per-event `apply()` tears are the class `recover_from_log` already TOLERATES
    (its own docstring: "Per-event guard: one bad event must not abort the whole
    recovery"), and only FOLD tears may gate `ok`. Collapsing the two (the
    cycle-5 `torn + _folds_torn` form) made a single un-appliable legacy line
    report `recovered: False`, and `_recover_or_raise` (projection/__init__.py:982)
    then RAISES — an embedded DB refuses to open over one bad event.

    Verified live: a parseable `EventRecorded` whose `object` is a dict raises
    `TypeError` in `_upsert_event`, and the pre-change code returned
    `{'recovered': True, 'reason': '… (1 skipped)'}`.
    """
    events = tmp_path / "events"; events.mkdir(exist_ok=True)
    sdk = TortoiseSDK(str(tmp_path / "pe.db"),
                      event_log_path=str(events / "events.jsonl"))
    sdk.create_entity("object", "pe", objectKind="core:other", is_episodic=False)
    # A parseable-but-un-appliable legacy line: `object` is a dict, and
    # `_upsert_event` expects a str.
    EventLog(str(events / "events.jsonl")).append(
        {"type": "EventRecorded", "eventId": "bad1", "object": {"nested": 1},
         "summary": "bad"})

    from tortoise.projection import FalkorProjection
    proj = FalkorProjection(str(tmp_path / "pe2.db"))
    proj.g.query("MATCH (n) DETACH DELETE n")
    try:
        res = recover_from_log(str(events), proj)
        assert res["recovered"] is True, (
            f"a per-event apply failure is TOLERATED (only fold tears gate ok): {res}")
        assert "skipped" in res["reason"], \
            "the per-event tear must still be REPORTED in the reason"
    finally:
        proj.close()


def test_fold_sweep_handles_2500_folds(tmp_path):
    """Scale regression: the sweep issues ONE Cypher round-trip per surviving fold.

    THE MEASURED ACCOUNT (one figure, one measurement, no history):
      - ~3.8–4.1 ms per fold on docker FalkorDB with 10k nodes resident
        (cycle 7; an earlier ~2.9 ms/fold figure was taken at 3000 folds on a
        smaller graph and understated the 10k case).
      - VERIFY-1 measured the flush at **23.45 s for 5000 folds**; the 10k form
        measured **124 s**. `N` is now **2500**: IMPLEMENTATION found N=5000
        failing at **60.4 s against the 60 s bound** when the test ran inside
        the full suite (vs 23.45 s standalone), i.e. it was flaky under load.
        The plan's own instruction is "prefer lowering N over raising the
        bound", and N=2500 still detects an order-of-magnitude regression
        (2500 folds at 10x ≈ 600 s).
      - Memory is not the constraint (0.3 MB for 3000 folds + anchors).

    VERIFY-2 P1 (slot 2) — THE REMNANT IS NOW DELETED, and this is worth stating
    plainly: the cycle-8 AND cycle-9 logs BOTH recorded this paragraph as
    "consolidated", and it was not. It still carried a dangling subject ("is
    deliberately loose" with no noun), a stale `folds = ~8.75 s` fragment, and
    THREE mutually inconsistent figures (~8.75 s, ~19 s, ~40 s) for the same
    flush. This is the single clearest instance in the whole document of the
    failure mode that made 11 review cycles necessary: a logged fix that never
    reached the body. The replacement above is one measurement for one N.

    The 60 s bound sees a 2.5–3x margin at N=5000. It is deliberately loose (CI
    variance) — it exists to catch an ORDER-OF-MAGNITUDE regression, e.g. an
    accidental nested query per fold, not a 20% drift. A batch form
    (`WHERE o.id IN $ids` after the survivor filter runs in Python) would cut F
    round-trips to 1; not in scope for #2977.

    CYCLE 5 — THE SETUP MUST NOT USE `_upsert_object`. That helper calls
    `compute_embedding(name)` unconditionally, so seeding 10k nodes this way is
    10k transformer forward passes: measured ~137-167 ms/node (~23-28 MINUTES for
    10 000), against a flush that itself takes a measured ~124 s at 10k folds (VERIFY-3 P2-3: this said `~19 s`, contradicting the MEASURED ACCOUNT above). The test would time out CI
    rather than catch a regression, and its runtime is environment-dependent
    (an absent embedder cache degrades to a fast no-op, so it is roughly 50x
    slower on a warm runner). Seed with raw Cypher — the flush only needs the
    anchor events plus the target nodes, and the plan's own unit tests already
    create Objects by raw `CREATE`.
    """
    import time
    from tortoise.projection import FalkorProjection
    # This is the ONE test that BYPASSES the class-level test redirect (epic
    # #1647 D-1=A): `from_uri` lands on the SHARED session graph rather than a
    # per-test `test_<stem>_<hash>` one. Its wall-clock bound was calibrated on
    # docker, which is why it opts in — and it must therefore clean up in a
    # `finally`, since the shared graph outlives this test.
    # VERIFY-1 P1-4 (Reviewer slot 1, EMPIRICAL): N is 5000, NOT 10000. Slot 1
    # ran the exact Task-8 Step 3 command twice; the 10k form measured **124 s**
    # against a 60 s bound (`assert 124.02 < 60.0`) on this hardware. The
    # docstring above already gives the instruction — "prefer lowering N over
    # raising the bound" — and it was not followed. At the measured ~4 ms/fold a
    # 5000-fold sweep is ~20 s, keeping a 3x margin on this machine and ~2x on a
    # 1.5x-slower CI runner, while still catching an order-of-magnitude
    # regression, which is the bound's only job.
    proj = FalkorProjection.from_uri(DB)
    proj.g.query("MATCH (n) DETACH DELETE n")
    try:
        folds, recreate = [], []
        for i in range(2_500):
            recreate.append((2 * i, {"type": "ObjectRegistered", "id": f"o{i}",
                                     "name": f"n{i}"}))
            folds.append((2 * i + 1, {"type": "ObjectRetracted", "id": f"o{i}",
                                      "name": f"n{i}", "ts": "T"}, "retract"))
        # Raw CREATE, batched — NOT _upsert_object (see the docstring above).
        BATCH = 500
        for start in range(0, 2_500, BATCH):
            proj.g.query(
                "UNWIND $rows AS r CREATE (:Object {id: r.id, name: r.name, "
                "status: 'live'})",
                params={"rows": [{"id": f"o{i}", "name": f"n{i}"}
                                  for i in range(start, min(start + BATCH, 2_500))]})
        t0 = time.monotonic()
        proj._flush_object_folds(folds, recreate)
        elapsed = time.monotonic() - t0
        assert elapsed < 60.0, (
            f"fold sweep took {elapsed:.1f}s for 2.5k folds — an order-of-magnitude "
            f"regression; consider batching")
    finally:
        # `from_uri(DB)` targets the SHARED session graph (see the Lane note) —
        # leaving 10k :Object nodes behind would poison every later test in the
        # session, so clean up here and not only at the start.
        try:
            proj.g.query("MATCH (o:Object) DETACH DELETE o")
        finally:
            proj.close()


def test_retracted_object_excluded_from_recall_even_with_superseded(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "t10.db"))
    sdk.create_entity("object", "hidden", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "hidden")
    sdk._get_proj()._fold_object_retracted({"id": oid, "name": "hidden", "ts": "T"})
    for include in (False, True):
        # recall_state returns list[dict], NOT a dict. Match on `content` (the
        # result dict's name slot is "content" — SearchResult.to_dict() emits no
        # `name` key) or on `id`; matching on `name` would be vacuously green.
        rows = sdk.recall_state("hidden", include_superseded=include)
        assert not [r for r in rows
                    if r.get("entity_type") == "object"
                    and (r.get("id") == oid or r.get("content") == "hidden")], \
            f"retracted Object leaked (include_superseded={include})"


def test_live_object_still_returned(tmp_path):
    """Positive control — a filter that hides everything must fail this."""
    sdk = TortoiseSDK(str(tmp_path / "t10e.db"))
    sdk.create_entity("object", "visible", objectKind="core:other", is_episodic=False)
    rows = sdk.recall_state("visible")
    assert [r for r in rows if r.get("content") == "visible"], \
        "a LIVE Object must still be returned — guard against a vacuous test"


def test_retracted_object_excluded_from_search(tmp_path):
    """Drive the real public surface. TortoiseSDK has NO `search()` method —
    the object-search entry point is tortoise_fts_query."""
    sdk = TortoiseSDK(str(tmp_path / "t10b.db"))
    sdk.create_entity("object", "hidden2", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "hidden2")
    sdk._get_proj()._fold_object_retracted({"id": oid, "name": "hidden2", "ts": "T"})
    hits = sdk.tortoise_fts_query("hidden2", entity_type="object")
    assert not [h for h in hits if h.get("id") == oid or h.get("content") == "hidden2"]


def test_retracted_object_excluded_from_structural_leg(tmp_path):
    """VERIFY-1 P1-1 (Reviewer slot 1, EMPIRICAL): Task 5's acceptance names
    "all four search_engine legs plus the sdk.py:12317 post-filter" as the
    pinned surface, but the plan's own tests reach only TWO legs. Slot 1 spied
    on `_status_vocab_for` during the plan's own object-search test and got
    `['Object', 'Object']` — FTS + vector-index only. The structural leg's
    conditions are empty unless `kind` is passed, and it short-circuits to `[]`.

    This test passes `kind` to force THAT leg to build its WHERE clause, so a
    transcription error in its `_exclude_status_clause` call is caught.
    """
    sdk = TortoiseSDK(str(tmp_path / "t10g.db"))
    sdk.create_entity("object", "leg-hidden", objectKind="core:other",
                      is_episodic=False)
    live_id = _entity_name_id("Object", "leg-hidden")
    sdk.create_entity("object", "leg-gone", objectKind="core:other",
                      is_episodic=False)
    gone_id = _entity_name_id("Object", "leg-gone")
    sdk._get_proj()._fold_object_retracted(
        {"id": gone_id, "name": "leg-gone", "ts": "T"})
    # VERIFY-2 P0-1 (slot 1, EMPIRICAL): `kind` must equal the stored
    # `objectKind`, NOT the `core:`-stripped short form. `run_structural_query`
    # builds `WHERE n.objectKind = $kind` (search_engine.py:798-821), so
    # `kind="other"` matched NOTHING and the live control returned `[]` —
    # making this test unsatisfiable. Probed live: `objectKind='core:other'`
    # with `kind="other"` -> `[]`; with `kind="core:other"` -> live hit
    # (`structural: 1.0`), and the retracted Object is correctly excluded.
    hits = sdk.tortoise_fts_query("leg", entity_type="object", kind="core:other")
    ids = {h.get("id") for h in hits}
    assert live_id in ids, "the structural leg must still return a LIVE Object"
    assert gone_id not in ids, (
        "the structural leg must exclude a retracted Object — if this is the "
        "only failure, that leg's `_exclude_status_clause` call is unparameterised")


def test_retracted_object_excluded_by_post_filter(tmp_path):
    """VERIFY-1 P1-1 (Reviewer slot 1): the `sdk.py:12317` post-filter is DEAD
    CODE under the plan's own tests — no test passes `exclude_status`, and every
    production caller that does uses `entity_type="point"`, so the `graph_label
    in ("Point", "Object")` edit is exercised by nothing.

    VERIFY-2 P1-1 (slot 1, EMPIRICAL): merely PASSING `exclude_status` is NOT
    enough. Slot 1 reverted the edit (`graph_label in ("Point","Object")` ->
    `graph_label == "Point"`) and this test STILL PASSED, because the FTS and
    vector legs already drop `retracted` Objects (Task 5(b) threads the Object
    vocabulary into them). The result set never contains the retracted id, so
    the post-filter is entered with nothing to filter — a GREEN BLIND TEST, the
    exact class this plan condemns elsewhere.

    The honest pin SPIES ON THE GRAPH, as the sibling
    `test_fold_object_retracted_skips_null_id_branch` does: record every query,
    then assert the post-filter query for `graph_label == "Object"` actually
    RAN. That is green only when the label gate admits Objects, and red when it
    reads `== "Point"` — i.e. it detects the only edit this test exists to pin.
    """
    sdk = TortoiseSDK(str(tmp_path / "t10h.db"))
    proj = sdk._get_proj()
    sdk.create_entity("object", "pf-gone", objectKind="core:other",
                      is_episodic=False)
    gone_id = _entity_name_id("Object", "pf-gone")
    proj._fold_object_retracted({"id": gone_id, "name": "pf-gone", "ts": "T"})

    class _RecordingGraph:
        def __init__(self, inner):
            self._inner = inner
            self.calls: list[str] = []
        def query(self, q, **kw):
            self.calls.append(q)
            return self._inner.query(q, **kw)

    rec = _RecordingGraph(proj.g)
    proj.g = rec
    try:
        # VERIFY-3 P0-2 (slot 1, EMPIRICAL) — `include_terminal=True` IS REQUIRED
        # here, and its absence made this test UNSATISFIABLE. Task 5(b) threads
        # the Object vocabulary into the FTS / vector-index / brute-force legs,
        # so with the default `include_terminal=False` those legs ALREADY drop
        # the retracted Object — `result_ids` is then empty, and the post-filter's
        # own `if exclude_status and result_ids and …` short-circuits before
        # running. Slot 1 confirmed: without this flag the assertion fails with
        # `Queries seen: […FTS…, …MATCH (n:Object) WHERE n.embedding…]` and no
        # post-filter query. WITH it, the legs skip their own filter, `result_ids`
        # is non-empty, the post-filter RUNS, the test passes on the correct gate
        # **and FAILS when the gate is reverted to `== "Point"`** — i.e. only now
        # is it falsifiable in the direction it claims to pin.
        hits = sdk.tortoise_fts_query(
            "pf-gone", entity_type="object",
            exclude_status=["retracted"], include_terminal=True)
    finally:
        proj.g = rec._inner
    assert any("MATCH (n:Object) WHERE n.id IN" in q for q in rec.calls), (
        "the sdk.py:12317 post-filter did not run for graph_label='Object' — "
        "either the label gate still reads `== \"Point\"` (the edit this test "
        "pins) or the block's try/except swallowed an error. Queries seen: "
        f"{[q[:60] for q in rec.calls]}")
    assert not [h for h in hits if h.get("id") == gone_id], (
        "the post-filter must drop a retracted Object")


def test_audit_optin_still_returns_retracted(tmp_path):
    """`include_terminal=True` is the documented full-scan opt-in; it routes to
    `excluded_statuses=()` at sdk.py:12036."""
    sdk = TortoiseSDK(str(tmp_path / "t10d.db"))
    sdk.create_entity("object", "audit-me", objectKind="core:other", is_episodic=False)
    oid = _entity_name_id("Object", "audit-me")
    sdk._get_proj()._fold_object_retracted({"id": oid, "name": "audit-me", "ts": "T"})
    hits = sdk.tortoise_fts_query("audit-me", entity_type="object", include_terminal=True)
    assert [h for h in hits if h.get("id") == oid], \
        "the audit opt-in must still see retracted Objects"


def test_outdated_object_stays_visible(tmp_path):
    """The Object clause must NOT carry the Point `outdated` conjunct."""
    sdk = TortoiseSDK(str(tmp_path / "t10c.db"))
    proj = sdk._get_proj()
    sdk.create_entity("object", "legacy", objectKind="core:other", is_episodic=False)
    proj.g.query("MATCH (o:Object {name:'legacy'}) SET o.status='live', o.outdated=true")
    hits = sdk.tortoise_fts_query("legacy", entity_type="object")
    assert [h for h in hits if h.get("content") == "legacy"], \
        "an outdated Object must stay visible — `outdated` is a Point-only flag"


def test_superseded_object_visibility_unchanged(tmp_path):
    """Pin the PRE-EXISTING behaviour: the four search legs applied no Object
    terminal-status filter before this plan. It adds `retracted` only — it must
    not silently start hiding superseded/deprecated/archived Objects.
    (That wider gap is filed as a follow-up in Task 7 (c).)"""
    sdk = TortoiseSDK(str(tmp_path / "t10f.db"))
    proj = sdk._get_proj()
    proj.g.query("CREATE (:Object {id:'sup1', name:'wassuperseded', "
                 "status:'superseded', objectKind:'core:other'})")
    hits = sdk.tortoise_fts_query("wassuperseded", entity_type="object")
    assert [h for h in hits if h.get("id") == "sup1"], (
        "narrowing the search view to {retracted} must leave superseded "
        "Objects visible exactly as before — widening is follow-up (c), not "
        "a silent side effect of this plan")


def test_object_visibility_vocabularies_are_declared_views():
    r"""#688 D4/D7: NOTHING asserted any pair of Object-visibility sets agrees
    (`grep -rn "_RECALL_OBJECT_EXCLUDED\\|OBJECT_TERMINAL\\|OBJECT_SEARCH" tests/`
    → 0 assertions, 1 comment). That absence was the finding.

    CYCLE 5 — STRENGTHENED. The first form was structurally blind in both
    directions: `_RECALL is OBJECT_TERMINAL` is an ALIAS identity (CPython's
    `frozenset(x)` returns `x` unchanged when `x` is already a frozenset, so a
    re-literalisation with the same values passed), and
    `OBJECT_SEARCH < OBJECT_TERMINAL` stays TRUE if the canonical set grows a
    member — i.e. exactly the drift this test exists to catch. Both are now
    explicit VALUE assertions, and `assembly.py`'s divergent set is IMPORTED
    and pinned as a NAMED divergence so aligning it is a deliberate, RED edit.
    """
    from tortoise.commit_ops import (OBJECT_TERMINAL_STATUSES,
                                     _RECALL_OBJECT_EXCLUDED_STATUS,
                                     OBJECT_SEARCH_EXCLUDED_STATUS)
    from tortoise.sdk import OBJECT_STATUS_VALUES
    import tortoise.assembly as _assembly

    # Values, not identities: a re-literalised alias must not slip through.
    assert OBJECT_TERMINAL_STATUSES == frozenset(
        {"superseded", "deprecated", "archived", "retracted"})
    assert _RECALL_OBJECT_EXCLUDED_STATUS == OBJECT_TERMINAL_STATUSES
    # The search view is a strict, DELIBERATE subset — pinned BY VALUE, so
    # growing the canonical set cannot silently widen search visibility.
    assert OBJECT_SEARCH_EXCLUDED_STATUS == frozenset({"retracted"})
    assert OBJECT_SEARCH_EXCLUDED_STATUS < OBJECT_TERMINAL_STATUSES
    # Every canonical status must be writable by some legitimate writer.
    assert OBJECT_TERMINAL_STATUSES <= OBJECT_STATUS_VALUES
    # assembly.py:1017's divergence is NAMED, not described in a comment:
    # changing either set breaks this line. Tracked in #2901; filed as (d).
    assert _assembly._RECALL_OBJECT_EXCLUDED_STATUSES == (
        OBJECT_TERMINAL_STATUSES | {"outdated"}), (
        "assembly's Object set diverged — decide deliberately, then update "
        "this assertion and the #2901/(d) deferral together")
