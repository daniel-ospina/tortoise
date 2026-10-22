"""#9391 — the replay window is HANDLE-ONLY, for the WHOLE window: it does not
open the raw-query lane, and a lane nested inside it cannot re-open it.

`FalkorProjection.query`'s `#7951` refusal used to be **scope-CONDITIONED** on
`_CACHE_WRITE_AUTHORIZED`, and `_replay_write_scope()` sets exactly that
ContextVar around a rebuild engine's body — so the public raw-query lane, the one
door that exists to write the cache WITHOUT the projection, was a permitted
writer for the duration of a rebuild:

    outside window: None            -> a mutating statement is REFUSED
    inside window : "replay"        -> the SAME statement was PERMITTED, and landed

Latent, not live (no engine calls `self.query`), which is exactly the
hidden-door class §17.1 closes: one accidental `self.query(...)` inside a
rebuild was a second writer no test would have noticed.

⛔ THE FIRST SHAPE OF THIS FIX WAS WINDOW-ONLY, AND THAT WAS NOT ENOUGH — the
measured finding of this file's own review round. Keying the closure on the
reason in flight only closes the UN-NESTED part of a rebuild: `rebuild_all` opens
`_replay_write_scope()` around its whole body and then opens
`_prewipe_restore_write_scope` / `_prewipe_synthetic_write_scope` per leg, and
`rebuild_from_record` calls `_ensure_indexes()`, which opens
`_graph_metadata_write_scope("schema-ddl")`. Because the reason lives in one
`ContextVar` and an inner scope SHADOWS the outer (the `#9210` contract), each of
those nests re-opened the raw lane. MEASURED on that shape: the mutating `CREATE`
landed under all three nests. The fix therefore carries the closure BESIDE the
lane reason (`_handle_only_write_scope` → `_RAW_QUERY_CLOSED_DEPTH`), so it
travels with the scope stack; `test_a_lane_nested_inside_the_window_cannot_reopen_the_raw_lane`
is the pin for that, and it is the test the window-only shape failed.

⛔ THE INSTRUMENT IS THE REAL DOOR, not a copy of its predicate. Seven of the nine
tests issue a real statement through `FalkorProjection.query` (the raw lane) or
`proj.g.query` (the write handle) on a journal-armed projection and read whether
it LANDED, and the handle tests flip the REAL switch (`_HANDLE_GUARD_ENFORCES`)
so "inside the window the handle still works" is measured against a REFUSING door
rather than a measuring one. The other TWO are STRUCTURAL and say so in their own
docstrings: `test_the_narrowing_is_declared_and_named_by_the_scope` (reads the
constants and `_lane_of`) and
`test_a_second_replay_declaration_site_carries_the_closure` (reads
`reestablish_watermark`'s source, because its window is internal and cannot be
entered from a test).

MUTATION — measured on the landed code, and stated for what it is. There are TWO
mechanisms and they are pinned SEPARATELY, which is the point of the next
paragraph:

* revert the raw-lane guard to `_CACHE_WRITE_AUTHORIZED.get() is None` (the
  pre-fix guard) → the window test and the nested-lane test both red (the
  regression the issue names);
* make `_handle_only_write_scope` set the lane reason WITHOUT the depth (the
  window-only shape this file's first review rejected) → the nested-lane test
  reds, the window test stays green;
* delete the `_replay_write_scope()` wrapper from `rebuild_from_record` → the
  rebuild-engines test reds (the armed handle refuses the fold);
* match the reason instead of the lane (`reason in _HANDLE_ONLY_WRITE_LANES`),
  monkeypatch the set, or drop the set check altogether → ONLY
  `test_the_lane_name_branch_narrows_without_a_window` reds. That is deliberate:
  `_RAW_QUERY_CLOSED_DEPTH` closes the whole replay window on its own, so the
  lane-name branch is NOT load-bearing there. It has NO production caller left in
  this tree either: both `replay` declaration sites (`_replay_write_scope()` and
  `event_store.reestablish_watermark`'s default) go through
  `_handle_only_write_scope`, which sets the depth. The branch is therefore
  DEFENCE-IN-DEPTH for a future bare `_cache_write_authorized(_REPLAY_WRITE_LANE)`
  with no window, and that test pins its SEMANTICS (the literal, a template, a
  monkeypatched member) rather than a live path;
  `test_a_second_replay_declaration_site_carries_the_closure` pins the second site
  structurally;
* `_lane_of` → identity → the declaration pin and the lane-name test red;
* dropping a member from `_HANDLE_ONLY_WRITE_LANES` → the declaration pin reds
  (member equality), and the lane-name test reds only when the dropped member is
  the one a live path declares.

NOT a reddening mutation by design, stated so it is not mistaken for a gap:
making the raw lane refuse unconditionally would leave the read test green (a
read is still a read) while changing six other lanes' width — that is `#9079`'s
call, not this file's.

WHAT IS *NOT* CLAIMED. This file narrows and measures the `replay` lane only.
Every lane NOT in `_HANDLE_ONLY_WRITE_LANES` still yields a raw-query permit
**when it is not nested inside a handle-only window** — amongst others the
pre-wipe restore/synthetic, apply-first, derived-recompute, graph-metadata,
onboarding, live-door, `webhook.delivery-store`, structural-edge and test-fixture
lanes — and nested inside one, the depth closes that lane too (which is the fix,
not an exception to it). That remaining width is `#9079`'s sanctioning decision:
a wholesale refusal at the raw lane is a measured P0 (it silently degraded a
hosted writer family — `tests/test_7951_enforce_atomic_write.py`).
"""
from __future__ import annotations

import pytest

from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    _HANDLE_ONLY_WRITE_LANES,
    _RAW_QUERY_CLOSED_DEPTH,
    _REPLAY_WRITE_LANE,
    DirectCacheWriteRefused,
    _cache_write_authorized,
    _graph_metadata_write_scope,
    _lane_of,
    _prewipe_restore_write_scope,
    _prewipe_synthetic_write_scope,
    _raw_query_lane_permit,
    _replay_write_scope,
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
    """The record view the rebuild-engines test projects: one `:Point`."""
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


def test_a_lane_nested_inside_the_window_cannot_reopen_the_raw_lane(armed_sdk):
    """The measured finding of this file's first review round: the closure must
    travel with the SCOPE STACK, not with the reason in flight.

    Each nest below is a real engine's shape, not a synthetic composition:
    `rebuild_all` opens the restore and synthetic lanes per leg INSIDE its
    replay window, and `rebuild_from_record` opens the schema-DDL lane for its
    indexes. On the window-only shape every one of them re-opened the raw lane
    (the `CREATE` landed) — so this test is what distinguishes the fix from its
    first, insufficient version.
    """
    proj = armed_sdk._get_proj()

    nests = [
        ("nest-restore", _prewipe_restore_write_scope("#9391 probe")),
        ("nest-synthetic", _prewipe_synthetic_write_scope("#9391 probe")),
        ("nest-schema-ddl", _graph_metadata_write_scope("schema-ddl")),
    ]
    for point_id, nest in nests:
        with _replay_write_scope(), nest:
            # The nested lane really is in flight — otherwise the test would
            # be measuring the outer window twice.
            assert _CACHE_WRITE_AUTHORIZED.get() != _REPLAY_WRITE_LANE, (
                f"{point_id}: the nested lane did not open, so this case "
                "would not exercise the shadowing it exists for")
            assert _raw_query_lane_permit() is None, (
                f"{point_id}: a lane nested inside the replay window "
                "yields a raw-query permit — the narrowing is window-only "
                "and the door #9391 closes is still open inside rebuild_all")
            with pytest.raises(DirectCacheWriteRefused):
                proj.query(_MUTATING % point_id)

    for point_id, _nest in nests:
        assert _point_count(proj, f"raw-lane-{point_id}") == 0, (
            f"raw-lane-{point_id} landed: a nested lane re-opened the raw-query "
            "lane inside the replay window")


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
        # ... including under a nested lane, which is where the restore legs and
        # the schema DDL actually write.
        with _prewipe_restore_write_scope("#9391 probe"):
            proj.g.query(_MUTATING % "handle-nested")

    assert _point_count(proj, "raw-lane-handle-inside") == 1, (
        "the replay window stopped authorizing the write handle — the rebuild "
        "engines would be refused on their own folds")
    assert _point_count(proj, "raw-lane-handle-nested") == 1, (
        "a nested lane stopped authorizing the write handle — the restore legs "
        "would be refused")


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

    A hand-maintained set of LANE names is only trustworthy if the names are the
    ones the scopes really set, and if the matcher can express a TEMPLATED lane
    (`pre-wipe-restore: <leg>` and four siblings are `f"{PREFIX}: {detail}"`, so
    an exact-string set would make adding one a silent no-op). Both are pinned
    here.
    """
    assert _REPLAY_WRITE_LANE == "replay"
    expected = frozenset({_REPLAY_WRITE_LANE})
    assert expected == _HANDLE_ONLY_WRITE_LANES, (
        "the handle-only lane set changed: "
        f"{sorted(_HANDLE_ONLY_WRITE_LANES)}")

    # The matcher is the LANE, not the reason — a templated member must bite.
    assert _lane_of(_REPLAY_WRITE_LANE) == _REPLAY_WRITE_LANE
    assert _lane_of(f"{_REPLAY_WRITE_LANE}: some detail") == _REPLAY_WRITE_LANE
    assert _lane_of(_REPLAY_WRITE_LANE) in _HANDLE_ONLY_WRITE_LANES

    with _replay_write_scope():
        assert _CACHE_WRITE_AUTHORIZED.get() == _REPLAY_WRITE_LANE, (
            "the scope's reason is not the literal the set names, so the "
            "narrowing would silently stop applying")


def test_the_lane_name_branch_narrows_without_a_window(armed_sdk, monkeypatch):
    """The lane-name branch is pinned by SEMANTICS, not by a live caller.

    `_RAW_QUERY_CLOSED_DEPTH` closes the whole replay window by itself, so the
    set is deliberately NOT load-bearing there (a mutation that drops it leaves
    the window tests green — stated in the module docstring). And after `#9391`
    it has no production caller left: both `replay` declaration sites
    (`_replay_write_scope()` and `event_store.reestablish_watermark`'s default)
    go through `_handle_only_write_scope`, which sets the depth, so the
    lane-name branch is never the reason a real write is refused. It is
    DEFENCE-IN-DEPTH for a future bare
    `_cache_write_authorized(_REPLAY_WRITE_LANE)` with no window — the shape
    this file's engine windows would otherwise have to trust. So the three
    things the set promises are measured here AT THE DOOR: the pinned literal, a
    TEMPLATE of it (which is what `_lane_of` exists for), and a member reached
    only through a monkeypatched set (which is what proves the guard reads the
    SET and the LANE, not one hard-coded string).
    """
    import tortoise.projection as projection

    proj = armed_sdk._get_proj()
    cases = [
        ("bare", _REPLAY_WRITE_LANE),
        ("templated", f"{_REPLAY_WRITE_LANE}: some detail"),
    ]
    for point_id, reason in cases:
        with _cache_write_authorized(reason):
            assert _RAW_QUERY_CLOSED_DEPTH.get() == 0, (
                "this case must exercise the lane-name branch, not the "
                "window depth")
            assert _raw_query_lane_permit() is None, (
                f"lane {reason!r} yields a raw-query permit — the "
                "handle-only lane branch is not applying")
            with pytest.raises(DirectCacheWriteRefused):
                proj.query(_MUTATING % f"lane-{point_id}")
        assert _point_count(proj, f"raw-lane-lane-{point_id}") == 0

    monkeypatch.setattr(projection, "_HANDLE_ONLY_WRITE_LANES",
                        frozenset({"probe-lane"}))
    with _cache_write_authorized("probe-lane: a detail"), \
            pytest.raises(DirectCacheWriteRefused):
        proj.query(_MUTATING % "lane-probe")
    assert _point_count(proj, "raw-lane-lane-probe") == 0, (
        "a lane named in `_HANDLE_ONLY_WRITE_LANES` did not narrow the raw lane "
        "— the guard is not reading the set through `_lane_of`")


def test_a_second_replay_declaration_site_carries_the_closure():
    """Every `replay` declaration must go through the HANDLE-ONLY helper.

    `_replay_write_scope()` is not the only site: `event_store.reestablish_watermark`
    declares the lane itself (its `MERGE` cannot be wrapped by its caller — an
    inner scope shadows an outer one, `#9210`). Declared the plain way, that
    window carries no closure, so a lane nested inside it re-opens the raw-query
    lane — the exact shape `#9391` closes. Structural, because the window is
    internal to the helper and cannot be entered from a test; it reds the moment
    that site reverts to a bare `_cache_write_authorized`.
    """
    import inspect

    from tortoise.event_store import reestablish_watermark

    source = inspect.getsource(reestablish_watermark)
    assert "_handle_only_write_scope(_REPLAY_WRITE_LANE)" in source, (
        "`reestablish_watermark` declares the replay lane WITHOUT the "
        "handle-only closure — a lane nested inside its window would re-open "
        "the raw-query lane (#9391)")


# ── the engine that opened the window ────────────────────────────────────


def test_the_rebuild_engines_still_project_inside_the_replay_window(
        armed_sdk, monkeypatch):
    """The recovery path still works with the handle ARMED.

    "The raw lane is refused" is only half a fix if the narrowing also refused
    the fold: this runs `rebuild_from_record` (the record→cache load §2.0
    depends on) end to end against a REFUSING handle and requires the node to
    materialise. It is also the engine that nests `graph-metadata:schema-ddl`
    inside the window, so it exercises the nested case with real code.
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
    writer. After the window: the raw lane is refused again, and both the
    ContextVar and the closure depth are back to their defaults."""
    proj = armed_sdk._get_proj()
    with _replay_write_scope(), _prewipe_restore_write_scope("#9391 probe"):
        pass

    assert _CACHE_WRITE_AUTHORIZED.get() is None
    assert _RAW_QUERY_CLOSED_DEPTH.get() == 0, (
        "the raw-lane closure leaked past its `with` — a closure that sticks is "
        "a widening for every later writer in this context")
    assert _raw_query_lane_permit() is None
    with pytest.raises(DirectCacheWriteRefused):
        proj.query(_MUTATING % "after")
    assert _point_count(proj, "raw-lane-after") == 0
