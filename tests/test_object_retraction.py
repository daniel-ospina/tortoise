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
