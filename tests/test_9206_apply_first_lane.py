"""#9206 — the APPLY-FIRST declaration: the SDK's own write doors state their order.

WHAT THIS SUITE IS FOR. §17.1's third door (`_GuardedGraph`, i.e. `proj.g`)
refuses a mutating statement that arrives with no declared lane. Before #9206
the SDK's apply-first doors declared NOTHING — their write precedes the record
that describes it — so arming the guard refused the product's own write path
(measured: 25 failed / 62 passed across the §17.1 battery, every refusal a
sanctioned SDK writer). #9206 adds the lane those doors can honestly claim:

    `_apply_first_write_scope` / `@_declares_apply_first_write_lane(reason)`

**What that lane claims, and what it does NOT.** It claims the cache write is
the COMMIT POINT and the journal record that describes it follows, BY DECISION —
the direction `delete_point` chose on measured evidence (#9207) and the shape
the promote arm of `update_point` cannot avoid (#8830). It does NOT bless an
unjournalled write: a door that appends no record is the #9068 defect, and the
census pin (`tests/test_9124_declared_live_doors.py::
test_every_apply_first_declared_door_states_an_order_and_a_record`) refuses it.

So this suite asserts the two behaviours the declarations exist for, on a REAL
armed handle:

1. each declared door COMPLETES and leaves the record it claims (the product
   still works under the guard); and
2. an UNDECLARED write through the same handle is still REFUSED (the lane is on
   the door, not on the statement — a declaration, not a bypass).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tortoise.projection import (
    _APPLY_FIRST_LANE,
    _CACHE_WRITE_AUTHORIZED,
    DirectCacheWriteRefused,
    _apply_first_write_scope,
    _declares_apply_first_write_lane,
    _replay_write_scope,
    _test_fixture_write_scope,
)


@pytest.fixture
def enforced(monkeypatch):
    """Arm the third door for one test (part 2's state, exercised deliberately).

    `_HANDLE_GUARD_ENFORCES` is a module constant and a code change to flip, not
    an env var or constructor argument — so a test flips it on the imported
    module, which is exactly the reference the handle reads at call time.
    """
    import tortoise.projection as projection

    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)
    return projection


@pytest.fixture
def armed_sdk(tmp_path):
    """A real SDK with a JSONL journal — the only state in which §17.1 is armed.

    `event_log_path` is what arms the handle (`_get_proj` -> `_bind_journal`) and
    an explicit `db_path` keeps the projection embedded when no URI is set.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "apply-first-9206.db"),
                      event_log_path=str(journal))
    sdk._journal_path_for_test = journal
    try:
        yield sdk
    finally:
        sdk.close()


def _journal_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def _records_since(sdk) -> list[str]:
    return [r.get("type") for r in _journal_lines(Path(sdk._journal_path_for_test))]


# ── the lane itself (unit) ────────────────────────────────────────────────

def test_the_lane_sets_a_reason_the_handle_reads_back():
    """The reason is the declaration's identity, readable AT the write handle.

    A lane whose name cannot be observed is a lane no test can prove was in
    flight, and the next reader cannot grep for it. The prefix is a literal
    (`_APPLY_FIRST_LANE`) precisely so this assertion can exist.
    """
    assert _CACHE_WRITE_AUTHORIZED.get() is None, (
        "the authorization must default to REFUSAL outside any lane")
    with _apply_first_write_scope("unit-test"):
        value = _CACHE_WRITE_AUTHORIZED.get()
    assert value == f"{_APPLY_FIRST_LANE}: unit-test", (
        f"the lane's reason is not the pinned prefix form: {value!r}")
    assert _CACHE_WRITE_AUTHORIZED.get() is None, (
        "the lane must be released on exit — a leaked authorization would cover "
        "the caller's next write")


def test_the_lane_releases_on_a_raising_door():
    """A door that raises must not leave the authorization armed.

    The failure mode is silent and durable: the scope is a ContextVar, so a lane
    leaked on the exception path authorizes every later write in that context.
    """
    with pytest.raises(RuntimeError), _apply_first_write_scope("raises"):
        raise RuntimeError("boom")
    assert _CACHE_WRITE_AUTHORIZED.get() is None


def test_a_decorated_door_declares_the_lane_for_its_whole_body():
    """The decorator authorizes the openers a `with` would — and names its reason."""

    @_declares_apply_first_write_lane("decorated-door")
    def door():
        return _CACHE_WRITE_AUTHORIZED.get()

    assert door() == f"{_APPLY_FIRST_LANE}: decorated-door"
    assert _CACHE_WRITE_AUTHORIZED.get() is None


def test_an_async_door_cannot_take_the_apply_first_lane():
    """An async wrapper would authorize NOTHING while looking like it did.

    Same failure the journal-first decorator refuses (`#9124`): the wrapper opens
    the scope, returns the coroutine and closes it, so the awaited body runs
    unauthorized. Loud at import time, never silent at run time.
    """

    async def door():  # pragma: no cover — never awaited; the decorator raises first
        pass

    with pytest.raises(TypeError, match="synchronous door"):
        _declares_apply_first_write_lane("async-door")(door)


# ── the doors still write, and still journal (integration, ARMED handle) ──

def test_update_point_writes_and_records_under_the_armed_guard(
        armed_sdk, enforced, force_sparse_tfidf):
    """`update_point` — BOTH arms.

    The non-promote arm appends first (its own journal-first ordering, unchanged)
    and the PROMOTE arm writes first (`#8830`), so the door states the weaker
    whole-door claim. Under an armed handle both must complete and both must
    leave a `PointRevised`.
    """
    sdk = armed_sdk
    pid = sdk.create_point("statement", "apply-first update_point")["id"]

    before = _records_since(sdk).count("PointRevised")
    sdk.update_point(pid, confidence=0.42)
    assert _records_since(sdk).count("PointRevised") == before + 1, (
        "the revision's record did not land")
    assert sdk.get_point(pid)["confidence"] == pytest.approx(0.42)

    # The promote arm: guarded write first, record after it (#8830/#7951 2b).
    draft = sdk.create_point("statement", "apply-first promote", status="draft")["id"]
    sdk.update_point(draft, status="live")
    assert sdk.get_point(draft)["status"] == "live", (
        "the declared apply-first door must not be refused at its guarded SET")


def test_update_entity_writes_and_records_under_the_armed_guard(
        armed_sdk, enforced, force_sparse_tfidf):
    """`update_entity` -> `_update_entity`: SET first, `EntityMutated` after."""
    sdk = armed_sdk
    obj = sdk.create_entity("object", "apply-first entity")["node"]["id"]

    before = _records_since(sdk).count("EntityMutated")
    sdk.update_entity(obj, status="active")
    assert _records_since(sdk).count("EntityMutated") == before + 1, (
        "the entity mutation's record did not land")
    assert sdk.get_entity(obj)["status"] == "active"


def test_create_operator_writes_and_records_under_the_armed_guard(
        armed_sdk, enforced, force_sparse_tfidf):
    """`create_operator`: CREATE the node + edges first, `OperatorAdded` after."""
    sdk = armed_sdk
    src = sdk.create_point("statement", "operator source")["id"]
    tgt = sdk.create_point("statement", "operator target")["id"]

    before = _records_since(sdk).count("OperatorAdded")
    created = sdk.create_operator("IMPL", src, [tgt], label="causes")
    assert _records_since(sdk).count("OperatorAdded") == before + 1, (
        "the operator's record did not land")
    assert sdk.get_point(created["id"])["is_operator"] is True


def test_create_direct_edge_writes_and_records_under_the_armed_guard(
        armed_sdk, enforced, force_sparse_tfidf):
    """`create_direct_edge`: the MERGE first, its record after."""
    sdk = armed_sdk
    src = sdk.create_point("statement", "direct edge source")["id"]
    tgt = sdk.create_point("statement", "direct edge target")["id"]

    journal = Path(sdk._journal_path_for_test)
    before = journal.read_text() if journal.exists() else ""
    out = sdk.create_direct_edge("IMPL", src, tgt, confidence=0.7)
    assert out["created"] is True
    after = journal.read_text() if journal.exists() else ""
    assert after != before, (
        "the direct edge landed with NO record — the lane would then be blessing "
        "an unjournalled cache write (#9068)")


def test_delete_point_and_delete_entity_write_and_record_under_the_armed_guard(
        armed_sdk, enforced, force_sparse_tfidf):
    """The two hard deletes: cache-first by decision, record after a landed destroy."""
    sdk = armed_sdk
    pid = sdk.create_point("statement", "apply-first delete_point")["id"]
    before = _records_since(sdk).count("EntityMutated")
    assert sdk.delete_point(pid) is True
    assert _records_since(sdk).count("EntityMutated") == before + 1

    obj = sdk.create_entity("object", "apply-first delete_entity")["node"]["id"]
    before = _records_since(sdk).count("EntityMutated")
    assert sdk.delete_entity(obj) is True
    assert _records_since(sdk).count("EntityMutated") == before + 1




def test_the_lane_is_not_a_bypass(armed_sdk, enforced):
    """A raw write outside every lane is STILL refused with the guard armed.

    The declaration lives on the door. If this ever passes, #9206 turned the lane
    into a general permit — the second writer §17.1 forbids.
    """
    proj = armed_sdk._get_proj()
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("MATCH (n:Point {id:$id}) SET n.x = 1", params={"id": "p1"})
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("CREATE (n:Point {id:'9206-bypass'})")


# ── the record must DESCRIBE the write it precedes (review P1, #9206) ───────
#
# `_write_session_and_turns` declares the JOURNAL-FIRST lane, so the record it
# appends must describe every property its `:Session` MERGE writes — otherwise
# the claim is true about the ORDER and false about the CONTENT, and a
# journal-only rebuild restores a node the live graph never had. Measured
# before these pins existed: a hosted capture came back with neither
# `machine_id` nor `model` (the API then serves null attribution).


def test_the_session_record_carries_every_property_its_merge_writes():
    """One invariant, checked against the source: MERGE props ⊆ record keys.

    Driven over the AST so the record's own docstrings (which quote the Cypher)
    are not read as code, and so a NEW conditional clause added to `merge_sets`
    reds here until it is also carried by `session_record`.
    """
    import ast
    import re

    from tortoise import sdk as sdk_mod

    tree = ast.parse(Path(sdk_mod.__file__).read_text())
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_write_session_and_turns")

    merge_props: set[str] = set()
    record_keys: set[str] = set()
    for node in ast.walk(fn):
        # `merge_sets.append("s.<prop>=…")` — the conditional clauses.
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "merge_sets"):
            for arg in node.args:
                text = arg.value if isinstance(arg, ast.Constant) else ""
                merge_props |= set(re.findall(r"\bs\.(\w+)\s*=", str(text)))
        # `session_record = {…}` / `session_record: dict = {…}` — the
        # unconditional keys (`_write_session_and_turns` uses an AnnAssign).
        if (isinstance(node, ast.AnnAssign)
                and isinstance(node.value, ast.Dict)
                and isinstance(node.target, ast.Name)
                and node.target.id == "session_record"):
            for key in node.value.keys:
                if isinstance(key, ast.Constant):
                    record_keys.add(str(key.value))
        if (isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Dict)
                and any(isinstance(t, ast.Name) and t.id == "session_record"
                        for t in node.targets)):
            for key in node.value.keys:
                if isinstance(key, ast.Constant):
                    record_keys.add(str(key.value))
        # `session_record["<key>"] = …` — the conditional keys.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "session_record"
                        and isinstance(target.slice, ast.Constant)):
                    record_keys.add(str(target.slice.value))

    # The UNCONDITIONAL clauses of the base field list.
    base = next(
        node for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "merge_sets"
                for t in node.targets))
    assert isinstance(base.value, ast.List), (
        "the base field list is no longer a literal list — this pin cannot read "
        "it, so re-derive the invariant instead of deleting it")
    for elt in base.value.elts:
        if isinstance(elt, ast.Constant):
            merge_props |= set(re.findall(r"\bs\.(\w+)\s*=", str(elt.value)))

    assert merge_props, "the pin read no MERGE properties — it would be vacuous"
    missing = sorted(merge_props - record_keys)
    assert not missing, (
        "the record does not describe every property the :Session MERGE "
        f"writes: {missing}. This door declares the JOURNAL-FIRST lane, so a "
        "journal-only rebuild would restore the node WITHOUT them (measured: "
        "machine_id/model). Add the key to `session_record` and the coalesce-"
        "preserving arm to `_fold_session_recorded`.")


def test_the_fold_restores_the_attribution_props(armed_sdk):
    """`machine_id`/`model` survive the fold — the record's other half.

    The source pin above proves the record CARRIES them; this proves the fold
    APPLIES them, set-if-absent exactly as the live `coalesce` does.

    ⚠️ The folds are driven inside `_replay_write_scope()`, the lane a rebuild
    opens around a journal record's fold. Without it this test is the ONE thing
    an ARMED battery reds on (`_fold_session_recorded` issues a raw `MERGE` at
    the handle — measured with `_HANDLE_GUARD_ENFORCES = True`), and a test that
    calls a fold must declare the lane the product calls it in.
    """
    proj = armed_sdk._get_proj()
    sid = "sess_9206_attribution"
    with _replay_write_scope():
        proj._fold_session_recorded({
            "type": "SessionRecorded", "id": sid, "turn_count": 2,
            "is_episodic": True, "harness": "pi",
            "machine_id": "machine-A", "model": "mdl-2026"})
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.machine_id, s.model",
        params={"sid": sid}).result_set
    assert rows and rows[0][0] == "machine-A" and rows[0][1] == "mdl-2026", (
        f"the fold dropped the attribution props the record carries: {rows!r}")

    # Set-if-absent, exactly like the live `coalesce`: a later producer cannot
    # relabel the machine that captured the session.
    with _replay_write_scope():
        proj._fold_session_recorded({
            "type": "SessionRecorded", "id": sid, "machine_id": "machine-B",
            "model": "mdl-OTHER"})
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.machine_id, s.model",
        params={"sid": sid}).result_set
    assert rows and rows[0][0] == "machine-A" and rows[0][1] == "mdl-2026", (
        "the fold is not first-writer-wins — a later record relabelled the "
        f"capture's machine/model, which the live coalesce would not: {rows!r}")


# ── the declared doors the runtime battery did NOT drive (#9206 review) ─────
#
# The suite above drives 7 of the declared doors. Four had NO armed exercise,
# and two of them can fail SILENTLY because their only caller swallows the
# refusal: `_materialize_session_source` is reached through `capture_session`,
# which wraps it in `except Exception -> warning`, so a guard refusal there left
# every assertion green. These drive the helper directly, where the refusal is
# un-swallowable, and give the two EP doors the runtime half their declarations
# were missing. `hosted_api._delete_session_sync` stays census-only on purpose:
# no journal-bound fixture exists for it (a hosted suite runs with no
# `event_log_path`, so §17.1 is inert there) — its declaration says so where it
# lives, and `_APPLY_FIRST_DECLARED_DOORS_BY_MODULE` pins the membership.


def test_materialize_session_source_writes_and_records_under_the_armed_guard(
        armed_sdk, enforced):
    """`_materialize_session_source`, driven DIRECTLY (armed handle).

    This is the door whose ORDER #9206 changed (its record now precedes
    `link_source_to_event`) and the one the runtime battery could not fail on.
    """
    sdk = armed_sdk
    sid = "sess_9206_source"
    before = _records_since(sdk).count("SourceCreated")
    sdk._materialize_session_source(
        sid, "evt_9206_source", "2026-10-20T00:00:00+00:00",
        [{"role": "user", "content": "materialize the session source"},
         {"role": "assistant", "content": "summary and topics are derived"}])

    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) "
        "RETURN s.sessionId, s.capturedAt, s.contentHash",
        params={"u": f"session:{sid}"}).result_set
    assert rows and rows[0][0] == sid, (
        "the typed session Source did not land — the door is declared and must "
        f"complete under an armed handle; rows={rows!r}")
    assert rows[0][2], f"the transcript hash did not land: rows={rows!r}"
    assert _records_since(sdk).count("SourceCreated") == before + 1, (
        "the Source landed with no record of it — the lane would then be "
        "blessing an unjournalled cache write (#9068)")


def test_the_ep_invalidation_doors_clear_and_record_under_the_armed_guard(
        armed_sdk, enforced):
    """`ep.invalidate_messages` and `ep.invalidate_factor_messages` (armed).

    Neither was driven under an armed handle anywhere in the battery, so both
    declarations had a static census pin and NO runtime exercise (#9206
    review). Each is asserted twice: the `REMOVE` LANDED (the message slots are
    gone) and its `EdgeMessageChanged` op=clear record followed — the record is
    stated over the statement's own committed set, which is why the lane is
    apply-first rather than journal-first.
    """
    from tortoise.ep import TortoiseEP

    sdk = armed_sdk
    src = sdk.create_point("statement", "ep door source")["id"]
    tgt = sdk.create_point("statement", "ep door target")["id"]
    sdk.create_operator("IMPL", src, [tgt], label="ep-door")
    proj = sdk._get_proj()

    def seed_messages() -> None:
        # FIXTURE: stale EP seeds on every IMPL|NAND edge, the state a
        # warm-start reads. Declared as a fixture, never as a product lane.
        with _test_fixture_write_scope("9206-ep-message-seed"):
            proj.g.query(
                "MATCH ()-[r:IMPL|NAND]->() SET r.msg_alpha=0.5, "
                "r.msg_beta=0.5, r.back_msg_alpha=0.5, r.back_msg_beta=0.5")

    def seeded_edges() -> int:
        rows = proj.g.query(
            "MATCH ()-[r:IMPL|NAND]->() RETURN count(r.msg_alpha)").result_set
        return int(rows[0][0]) if rows else 0

    ep = TortoiseEP(proj, emit=sdk._emit_event)

    seed_messages()
    assert seeded_edges() >= 1, "fixture precondition: no seeding happened"
    before = _records_since(sdk).count("EdgeMessageChanged")
    cleared = ep.invalidate_messages()
    assert cleared >= 1, "the full drop must match the seeded edges"
    assert seeded_edges() == 0, (
        "the REMOVE did not land under the armed handle — the declaration is "
        "what lets the drop reach the handle at all")
    assert _records_since(sdk).count("EdgeMessageChanged") == before + 1, (
        "the drop landed with no record — a rebuild would resurrect the seeds "
        "the live graph just cleared (#330/#5380)")

    seed_messages()
    before = _records_since(sdk).count("EdgeMessageChanged")
    cleared = ep.invalidate_factor_messages([src])
    assert cleared >= 1, (
        "the operator-neighbourhood drop must match the seeded edges")
    assert seeded_edges() == 0
    assert _records_since(sdk).count("EdgeMessageChanged") == before + 1, (
        "the neighbourhood drop landed with no record (#2422's ghost votes "
        "would come back on replay)")


# ── the DISCLOSED window: an orphan record wins the replay attribution ───────
#
# `_write_session_and_turns` appends its `SessionRecorded` BEFORE its `:Session`
# MERGE — that is what makes the MERGE journal-first — and the append is
# best-effort while the MERGE raises after its bounded retries. So an ABORTED
# MERGE leaves an orphan record, and every coalesce-preserved field is
# first-writer-wins on BOTH sides. This is NOT the "a record the cache lacks is
# projection lag" residual (lag converges on replay); it is its own, smaller
# window, disclosed at the emit site and in §17.1's ledger, and owned by #9390.


def test_an_orphan_record_wins_the_replay_fold_attribution_measured(
        armed_sdk, enforced, monkeypatch):
    """MEASURE the window #9390 owns, so it cannot change silently.

    Sequence, all through the real writer:

    1. attempt 1 appends `SessionRecorded{capture_lane="hook",
       machine_id="machine-A"}` and then its `:Session` MERGE ABORTS → no node;
    2. attempt 2 (the `#3516` piece-7 store-sync backstop, a DIFFERENT lane)
       appends and its MERGE lands → live holds `store_sync` / `machine-B`;
    3. the fold, applied in journal order as a journal-only rebuild does, keeps
       attempt 1's values — replay attributes the session to a hook capture that
       never wrote a row.

    This asserts the CURRENT, DISCLOSED behaviour rather than the desired one:
    before #9206 the record was emitted from the post-MERGE callback, so attempt
    1 left no record and the fold saw only `store_sync` — which is what makes it
    non-vacuous. If #9390 lands a fix (an attempt/instant the fold can order a
    LANDED record by), the last assertion flips — a reviewed change to a
    measured window, which is the point of pinning it.
    """
    from tortoise import sdk as sdk_mod
    from tortoise.sdk import TortoiseSDK

    sdk = armed_sdk
    proj = sdk._get_proj()
    sid = "sess_9206_orphan"
    now = "2026-10-20T00:00:00+00:00"
    turns = [{"role": "user", "content": "the hook tried first"}]

    real = sdk._graph_write_with_retry
    fail = {"next": True}

    def abort_the_merge(fn, what=None, **kwargs):
        if what == "capture :Session MERGE" and fail["next"]:
            fail["next"] = False
            raise RuntimeError("engine aborted the :Session MERGE")
        return real(fn, what=what, **kwargs)

    monkeypatch.setattr(sdk, "_graph_write_with_retry", abort_the_merge)

    with pytest.raises(RuntimeError):
        sdk_mod._write_session_and_turns(
            proj, sdk, sid, turns, now=now,
            capture_lane="hook", machine_id="machine-A")

    # The orphan half: a record exists, the node does not.
    orphans = [r for r in _journal_lines(Path(sdk._journal_path_for_test))
               if r.get("type") == "SessionRecorded" and r.get("id") == sid]
    assert len(orphans) == 1 and orphans[0]["capture_lane"] == "hook", (
        "the aborted attempt left no orphan record — the writer is not "
        f"journal-first, so this window does not exist: {orphans!r}")
    before = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN count(s)",
        params={"sid": sid}).result_set
    assert before and int(before[0][0]) == 0, (
        f"the aborted MERGE created a node anyway: {before!r}")

    # Attempt 2: the store-sync backstop, a different lane on the same session.
    sdk_mod._write_session_and_turns(
        proj, sdk, sid, turns, now="2026-10-20T00:05:00+00:00",
        capture_lane="store_sync", machine_id="machine-B")

    live = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.capture_lane, s.machine_id",
        params={"sid": sid}).result_set
    assert live and live[0][0] == "store_sync" and live[0][1] == "machine-B", (
        f"precondition: LIVE must hold the landed attempt's attribution: {live!r}")

    # The replay leg: the fold a journal-only rebuild drives, applied in journal
    # order into a fresh graph (no destructive wipe needed to measure the fold).
    records = [r for r in _journal_lines(Path(sdk._journal_path_for_test))
               if r.get("type") == "SessionRecorded" and r.get("id") == sid]
    # THREE, not two: the turn store emits its own trailing `SessionRecorded`
    # (the redaction count, #4911) after the shared writer's.
    assert len(records) >= 2 and records[0].get("capture_lane") == "hook" \
        and records[1].get("capture_lane") == "store_sync", (
        f"expected the orphan THEN the landed attempt's record: {records!r}")

    replay = TortoiseSDK(str(Path(sdk._journal_path_for_test).parent / "replay.db"))
    try:
        replay_proj = replay._get_proj()
        for record in records:
            replay_proj._fold_session_recorded(record)
        folded = replay_proj.g.query(
            "MATCH (s:Session {id:$sid}) RETURN s.capture_lane, s.machine_id",
            params={"sid": sid}).result_set
    finally:
        replay.close()

    assert folded and folded[0][0] == "hook" and folded[0][1] == "machine-A", (
        "the DISCLOSED window (#9390) changed shape: a journal-only rebuild no "
        "longer restores the orphan's attribution. If that is a fix, say so "
        "where it landed (the emit-site comment and §17.1's ledger name it) — "
        f"live={live!r} replay={folded!r}")
