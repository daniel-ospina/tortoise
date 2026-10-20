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


def test_capture_session_writes_and_records_both_halves_under_the_armed_guard(
        armed_sdk, enforced, force_sparse_tfidf):
    """The capture path: `:Session` MERGE + turn store, and their records.

    This is the door whose undeclared `:Session` MERGE caused 72 of the 97
    refusals measured on #9206. Two records are asserted, because the lane's
    honesty rests on them: `SessionRecorded` (appended by the shared writer
    BEFORE its MERGE — journal-first for that statement) and `PointAdded` for the
    turn Points (`_write_capture_turns`, which declares the apply-first lane).
    """
    sdk = armed_sdk
    out = sdk.capture_session([
        {"role": "user", "content": "apply-first capture: does it write?"},
        {"role": "assistant", "content": "it does, and it journals both halves."},
    ], session_id="sess_9206_lane")

    assert out["session_id"] == "sess_9206_lane"
    assert out["turns"] == 2
    types = _records_since(sdk)
    assert "SessionRecorded" in types, (
        "the :Session MERGE landed with no record of it — a journal-only "
        f"rebuild would drop the node (records={types})")
    assert types.count("PointAdded") >= 2, (
        f"the turn Points landed with no records (records={types})")


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
