"""#9391 — the replay window is HANDLE-ONLY: it does not open the raw-query lane.

`FalkorProjection.query`'s `#7951` refusal is **scope-CONDITIONED** — it fires
only while `_CACHE_WRITE_AUTHORIZED` is `None`:

```python
if self._journal_backed and _CACHE_WRITE_AUTHORIZED.get() is None \\
        and _statement_writes(cypher):
    raise DirectCacheWriteRefused(...)
```

and `_replay_write_scope()` sets exactly that ContextVar. Measured on the
pre-fix tree (`main@26aff163c`, re-measured here):

    outside window: None            -> a mutating statement is REFUSED
    inside window : replay          -> the SAME statement was PERMITTED, and landed

So for the whole replay window of `rebuild_all`, `rebuild`, `rebuild_from_record`
and the event-store rebuild, the public raw-query lane — the one door that exists
to write the cache WITHOUT the projection — was a permitted writer. Latent, not
live (no engine calls `self.query`), which is exactly the hidden-door class
§17.1 exists to close: one accidental `self.query(...)` was a second writer that
no test in the tree would have noticed.

⛔ WHAT THIS SUITE NOW CLAIMS, and how it is measured. The narrowing is a
property of the LANE, not of any engine's body: `_HANDLE_ONLY_WRITE_LANES` names
the reasons that authorize the write handle (`proj.g`) and every guarded client
handle below it but NOT `FalkorProjection.query`, and `_raw_query_lane_permit()`
is the one reader the raw-lane guard consults. Each test measures the REAL door:

* T1 reads the refusal (pre-fix: the statement landed — RED);
* T2 flips the REAL switch (`_HANDLE_GUARD_ENFORCES`) and proves the handle the
  rebuild engines actually write through is still authorized inside the window —
  the narrowing must not be bought by closing the lane the recovery path needs;
* T3 proves the narrowing is WRITE-only (a read is not a write);
* T4 pins the lane set to its single member and pins the member to the reason
  `_replay_write_scope()` really emits, so the set cannot drift from the scope;
* T5 runs the engine end to end with the handle ARMED, so "the rebuild still
  works" is measured rather than inferred;
* T6 proves the permit does not outlive its `with` (a narrowing that leaked would
  be a widening).

MUTATION (measured, each reddens this file): drop `"replay"` from
`_HANDLE_ONLY_WRITE_LANES` → T1 and T4 red (the lane is permitted again); make
`FalkorProjection.query` refuse unconditionally regardless of the lane → T3
still passes but the *other* lanes' raw-lane behaviour is unchanged, so this file
deliberately does NOT claim their width; delete the `_replay_write_scope()`
wrapper from `rebuild_from_record` → T5 red (the armed handle refuses the fold).

WHAT IS *NOT* CLAIMED. This file does not narrow, and does not measure, any lane
OTHER than `replay`: the pre-wipe restore/synthetic, apply-first,
derived-recompute, graph-metadata, onboarding and live scopes still open the
raw-query lane, and that width is left where `#9079`'s sanctioning decision owns
it. It also does not claim `FalkorProjection.query` is closed for good — only
that no lane in `_HANDLE_ONLY_WRITE_LANES` opens it, and the set is pinned
member-exactly so adding one is a deliberate act.
"""
from __future__ import annotations

import pytest

from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    _HANDLE_ONLY_WRITE_LANES,
    _REPLAY_WRITE_LANE,
    _raw_query_lane_permit,
    _replay_write_scope,
    DirectCacheWriteRefused,
)

# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def armed_sdk(tmp_path):
    """A real SDK with a configured JSONL journal, so §17.1 is ARMED.

    Same fixture shape as `tests/test_9079_record_replay_declared.py`: an
    explicit `db_path` makes the projection embedded when no URI is configured,
    and a URI-set session is redirected to its server. `event_log_path` is what
    arms §17.1 at the fold, the raw-query lane and the handle.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "replay_window.db"),
                      event_log_path=str(journal))
    try:
        yield sdk
    finally:
        sdk.close()


def _view():
    """The record view T5 projects: one `:Point`, no relations."""
    from tortoise import cache_projection as cp

    return cp.RecordView(
        nodes=(cp.node_from_row(
            "Point", {"id": "p-9391", "content": "a projected claim",
                      "point_kind": "claim", "status": "live",
                      "props": {}}),),
        relations=(),
    )


def _point_count(proj, point_id: str) -> int:
    return int(proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)",
        params={"id": point_id},
    ).result_set[0][0])


#: One mutating statement, used identically outside and inside the window so the
#: two measurements differ in exactly one variable (the open lane).
_MUTATING = "CREATE (n:Point {id:'raw-lane-%s'})"


# ── the width, at the raw-query lane's own guard ─────────────────────────


def test_the_replay_window_does_not_open_the_raw_query_lane(armed_sdk):
    """RED pre-fix: the replay window authorized the public raw-query lane.

    Two halves, so a passing test cannot be a vacuous one: the SAME statement is
    refused outside the window (the guard is armed and this fixture measures it),
    and refused inside it (the window is handle-only). The second half is the
    fix; the first half is what makes "refused inside" mean something other than
    "the guard is off in this configuration".
    """
    proj = armed_sdk._get_proj()
    assert proj._journal_backed, (
        "the fixture must ARM §17.1 (`event_log_path` configured); on an unbound "
        "projection the raw lane refuses nothing and this test would be vacuous")

    with pytest.raises(DirectCacheWriteRefused):
        proj.query(_MUTATING % "outside")
    assert _point_count(proj, "raw-lane-outside") == 0, (
        "the refusal outside the window must be PRE-write")

    with _replay_write_scope():
        # The window IS open — measured, not assumed: a test that asserted a
        # refusal with no permit in flight would be testing the wrong thing.
        assert _CACHE_WRITE_AUTHORIZED.get() == _REPLAY_WRITE_LANE
        assert _raw_query_lane_permit() is None, (
            "the replay lane must not be readable as a raw-query permit")
        with pytest.raises(DirectCacheWriteRefused):
            proj.query(_MUTATING % "inside")

    assert _point_count(proj, "raw-lane-inside") == 0, (
        "a mutating statement landed on the public raw-query lane INSIDE the "
        "replay window — the window is a permit for the HANDLE a rebuild writes "
        "through, not for the door that bypasses the projection (§17.1, #9391)")


def test_the_replay_window_still_opens_the_write_handle(armed_sdk, monkeypatch):
    """The narrowing must not be bought by closing `proj.g`.

    `rebuild`/`rebuild_all`/`rebuild_from_record` write the cache through the
    handle, so the lane has to keep authorizing it. Measured at the REAL door:
    `_HANDLE_GUARD_ENFORCES` is flipped (part 2's state) so a missing permit
    raises instead of being counted, and the write is observed to LAND.
    """
    import tortoise.projection as projection

    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)

    proj = armed_sdk._get_proj()
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query(_MUTATING % "handle-outside")

    with _replay_write_scope():
        proj.g.query(_MUTATING % "handle-inside")

    assert _point_count(proj, "raw-lane-handle-inside") == 1, (
        "the replay window stopped authorizing the write handle — the rebuild "
        "engines would be refused on their own folds")


def test_reads_still_pass_on_the_raw_lane_inside_the_window(armed_sdk):
    """A READ is not a write: narrowing the permit must not make the raw lane a
    read-blocker, which would break every diagnostic run inside a rebuild."""
    proj = armed_sdk._get_proj()
    with _replay_write_scope():
        assert proj.query(
            "MATCH (n:Point) RETURN count(n)").result_set[0][0] == 0


# ── the declaration the guard reads ──────────────────────────────────────


def test_the_narrowing_is_declared_and_named_by_the_scope():
    """The set the guard reads and the reason the scope emits cannot drift.

    A hand-maintained set of lane names is only trustworthy if the names are the
    ones the scopes really set; `_REPLAY_WRITE_LANE` is that literal, and the
    membership assertion makes ADDING a lane a deliberate, reviewable act rather
    than a side effect of a new scope.
    """
    assert _REPLAY_WRITE_LANE == "replay"
    assert _HANDLE_ONLY_WRITE_LANES == frozenset({_REPLAY_WRITE_LANE}), (
        "the handle-only lane set changed: " f"{sorted(_HANDLE_ONLY_WRITE_LANES)}")

    with _replay_write_scope():
        assert _CACHE_WRITE_AUTHORIZED.get() == _REPLAY_WRITE_LANE, (
            "the scope's reason is not the literal the set names, so the "
            "narrowing would silently stop applying")


# ── the engine that opened the window ────────────────────────────────────


def test_the_rebuild_engines_still_project_inside_the_replay_window(
        armed_sdk, monkeypatch):
    """The recovery path still works with the handle ARMED.

    "The raw lane is refused" is only half a fix if the narrowing also refused
    the fold: this runs `rebuild_from_record` (the record→cache load §2.0
    depends on) end to end against a REFUSING handle and requires the node to
    materialise.
    """
    import tortoise.projection as projection

    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)

    proj = armed_sdk._get_proj()
    report = proj.rebuild_from_record(_view(), confirm_destructive=True)

    assert report["nodes_written"] == 1
    assert _point_count(proj, "p-9391") == 1, (
        "the replay window no longer authorizes the rebuild's own fold")


def test_the_narrowed_permit_does_not_outlive_its_with(armed_sdk):
    """A permit that stuck would be a widening, and it would authorize the NEXT
    writer. After the window: the raw lane is refused again, and the ContextVar
    is back to `None`."""
    proj = armed_sdk._get_proj()
    with _replay_write_scope():
        pass

    assert _CACHE_WRITE_AUTHORIZED.get() is None
    assert _raw_query_lane_permit() is None
    with pytest.raises(DirectCacheWriteRefused):
        proj.query(_MUTATING % "after")
    assert _point_count(proj, "raw-lane-after") == 0
