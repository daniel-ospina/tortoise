"""#9079 — the RECORD-SOURCED rebuild engine declares §17.1's lane.

`FalkorProjection.rebuild_from_record` is the §2.0 record→cache load: it wipes
the graph, creates the indexes, folds the record's rows through
`cache_projection.project_records` and writes the known-hot declaration. Every
one of those writes goes through `proj.g` — §17.1's THIRD door. `rebuild` and
`rebuild_all` have opened `_replay_write_scope()` since `#7951` / `#9224`; this
engine did not, so it was the one rebuild path §17.1 still refused — i.e. the
recovery/loading path a lost cache depends on.

⛔ THE INSTRUMENT IS THE HANDLE'S OWN, not a copy of its predicate. Since `#9124`
the guard at `_GuardedGraph._refuse_unauthorized_cache_write` is landed in
MEASURE mode (`_HANDLE_GUARD_ENFORCES is False`): a mutating statement that
reaches the handle with no declared lane is counted in
`undeclared_handle_writes()` and logged; flipping the switch makes the same
statement raise `DirectCacheWriteRefused`. So:

* `test_the_record_projection_declares_its_lane` reads the CENSUS the
  declaration programme consumes (`undeclared_handle_writes()`), which is the
  runtime measurement — not a hand-copied predicate that can drift from the
  guard it claims to reproduce;
* the armed tests flip the REAL switch (`tests/test_9079_handle_write_authority.py`
  carries the `enforced` fixture for the guard itself; this file does the same
  one-line monkeypatch, because the point here is that the ENGINE is not
  refused, not that the guard refuses).

MUTATION (measured): delete the `with _replay_write_scope():` wrapper from
`rebuild_from_record` and all four tests below red — T1 counts the undeclared
statements (`MERGE (n:Meta …)` and `MERGE (n:Point …)`, one each), T2 observes
the lane as `None` at the fold (it is the UNARMED one, so it reads the lane
rather than a refusal), and T3/T4 raise `DirectCacheWriteRefused` on that same
fold (both flip `_HANDLE_GUARD_ENFORCES` for real). That is exactly the pre-fix
tree, so no test here is vacuous.

WHAT IS *NOT* CLAIMED. This suite is about `rebuild_from_record` only: the
event store's writes and `rebuild_all` are already declared (#9224), and this
file does not re-pin them. The scope's WIDTH (the raw-query lane is authorized
inside any rebuild window, since `FalkorProjection.query`'s refusal is
scope-conditioned) is stated at the engine, and narrowing it belongs to a lane
the rebuild engines would own — it is not asserted here as a property.
"""
from __future__ import annotations

import pytest

from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    DirectCacheWriteRefused,
    _reset_undeclared_handle_writes,
    undeclared_handle_writes,
)

# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def armed_sdk(tmp_path):
    """A real SDK with a configured JSONL journal, so §17.1's guard is ARMED.

    Lane-agnostic, like `tests/test_9079_handle_write_authority.py`'s
    `armed_sdk`: an explicit `db_path` makes the projection embedded when no URI
    is configured, and a URI-set session is redirected to its server
    (`FalkorProjection.__init__`'s test redirect). `event_log_path` is what arms
    §17.1 at the handle (`_get_proj` → `_bind_journal`).
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "record_replay.db"),
                      event_log_path=str(journal))
    try:
        yield sdk
    finally:
        sdk.close()


def _view():
    """The record view every test projects: one `:Point`, no relations."""
    from tortoise import cache_projection as cp

    return cp.RecordView(
        nodes=(cp.node_from_row(
            "Point", {"id": "p-rec-1", "content": "a projected claim",
                      "point_kind": "claim", "status": "live",
                      "props": {}}),),
        relations=(),
    )


def _point_count(proj, point_id: str) -> int:
    return int(proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)",
        params={"id": point_id},
    ).result_set[0][0])


# ── the declaration ──────────────────────────────────────────────────────


def test_the_record_projection_declares_its_lane(armed_sdk):
    """Every write `rebuild_from_record` issues reaches the handle DECLARED.

    RED pre-fix: the wipe and the index DDL declare their own lanes, but
    `project_records`'s folds and `write_declaration`'s write run with
    `_CACHE_WRITE_AUTHORIZED` `None`, so the handle records them as undeclared
    cache writes — the exact population `#9124`'s flip gate would start refusing.
    """
    proj = armed_sdk._get_proj()
    assert proj._journal_backed, (
        "the fixture must ARM §17.1 (`event_log_path` configured); on an unbound "
        "projection the handle measures nothing and this test would be vacuous")

    # The SDK's own construction writes (schema DDL) precede the measurement.
    _reset_undeclared_handle_writes()

    report = proj.rebuild_from_record(_view(), confirm_destructive=True)

    undeclared = undeclared_handle_writes()
    assert not undeclared, (
        "`rebuild_from_record` reached the write handle with NO declared lane "
        "for these statement(s), so arming the door would refuse the §2.0 "
        "record→cache load: "
        + " | ".join(f"{count}x {statement[:110]}"
                     for statement, count in sorted(undeclared.items())))

    # ... and the refusal would be a REFUSAL OF REAL WORK, not of a no-op: the
    # projection has to have landed.
    assert report["source"] == "record"
    assert report["nodes_written"] == 1
    assert _point_count(proj, "p-rec-1") == 1, (
        "the projection must still materialise the record's node")


def test_the_lane_the_projection_declares_is_the_rebuild_lane(armed_sdk,
                                                              monkeypatch):
    """The lane is `replay` — the one `rebuild`/`rebuild_all` declare.

    "Some lane was open" is weaker than the claim: a projection of the RECORD is
    a rebuild of the same derived state, so it must ride the same lane rather
    than a new one. Observed at `project_records`, i.e. at the fold that would be
    refused if no lane were open — `None` pre-fix.
    """
    from tortoise import cache_projection as cp

    seen: list[str | None] = []
    original = cp.project_records

    def _observed(g, plan):
        seen.append(_CACHE_WRITE_AUTHORIZED.get())
        return original(g, plan)

    monkeypatch.setattr(cp, "project_records", _observed)

    armed_sdk._get_proj().rebuild_from_record(_view(),
                                              confirm_destructive=True)

    assert seen == ["replay"], (
        "the record projection must run inside `_replay_write_scope()` (lane "
        f"'replay'); observed lanes {seen}")


# ── the door it must not be refused BY ───────────────────────────────────


def test_an_armed_door_does_not_refuse_the_record_projection(armed_sdk,
                                                             monkeypatch):
    """With `_HANDLE_GUARD_ENFORCES` True — part 2's state, the REAL switch —
    `rebuild_from_record` completes and materialises the node.

    RED pre-fix: `project_records`'s first fold raises `DirectCacheWriteRefused`,
    i.e. arming §17.1 breaks the record→cache load.
    """
    import tortoise.projection as projection

    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)

    proj = armed_sdk._get_proj()
    report = proj.rebuild_from_record(_view(), confirm_destructive=True)

    assert report["nodes_written"] == 1
    assert _point_count(proj, "p-rec-1") == 1


def test_the_declaration_is_not_a_global_bypass(armed_sdk, monkeypatch):
    """Lane WIDTH, at the same armed door.

    A declaration that outlived its `with` would be a bypass. Two claims: the
    ContextVar is back to `None` once the rebuild returns, and the same armed
    handle still refuses an undeclared `:Point` write — pre-write, so nothing
    lands (the `#9068` shape).
    """
    import tortoise.projection as projection

    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)
    proj = armed_sdk._get_proj()
    proj.rebuild_from_record(_view(), confirm_destructive=True)

    assert _CACHE_WRITE_AUTHORIZED.get() is None, (
        "the rebuild's lane leaked past its `with` — a declaration that sticks "
        "is a bypass, and it would authorize the NEXT writer")

    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("CREATE (n:Point {id:'undeclared-after-the-rebuild'})")
    assert _point_count(proj, "undeclared-after-the-rebuild") == 0, (
        "the refusal must be PRE-write — a guard that raises after dispatch "
        "leaves the corruption it claims to prevent")
