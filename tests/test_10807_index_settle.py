"""The catalogue-settle wait two fixtures share (#10807) — pinned without a server.

WHY THIS FILE EXISTS
--------------------
`tests/_index_settle.index_rows_after_settle` is the ONE definition of "wait until
the FalkorDB index catalogue has SETTLED", shared by two fixtures that each lost
the same race in a different file:

  * `tests/test_9079_handle_write_authority.py` drops `:Point(pointKind)` and then
    reads the catalogue to prove the drop — and a rebuild in flight still lists
    the retired field, so the read said "the drop did not happen" about a drop
    that had (`test (g)`, run 41817275671, on a loaded CI runner).
  * `tests/test_search_engine.py` drops `:Object(name)` FULLTEXT and asserts
    `_schema_is_current() is False` — the same async retirement, read through the
    product's own probe, which correctly answered True about a drift that had not
    been APPLIED yet (`test (b)`, run 41868531069).

Neither failure is reproducible on request (both windows are milliseconds on an
idle lane and outlive the read only under CI load), so the MECHANISM is pinned
here with a scripted fake: no server, no timing, no flake. What the live lanes
prove is that the fixtures call it; this file proves it waits for the right
thing, and that a bound which expires returns the LAST read rather than a
tolerant pass — the property both callers' unchanged assertions depend on.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from tests._index_settle import (
    INDEX_SETTLE_TIMEOUT_S,
    index_cells,
    index_kind_absent,
    index_rows_after_settle,
    point_index_drops_visible,
)

#: The catalogue row shape `CALL db.indexes()` yields: [label, properties,
#: {prop: [kind, ...]}, ...]. Only the first three columns are read by the
#: helper, so the fakes carry exactly those (a fuller row adds nothing here).
_OBJECT_WITH_FULLTEXT = [
    ["Object", ["id", "name"],
     {"id": ["RANGE"], "name": ["RANGE", "FULLTEXT"]}],
]
_OBJECT_WITHOUT_FULLTEXT = [
    ["Object", ["id", "name"], {"id": ["RANGE"], "name": ["RANGE"]}],
]
_POINT_REBUILDING = [
    ["Point", ["id", "pointKind"], {"id": ["RANGE"], "pointKind": ["RANGE"]},
     None, "english", [], "NODE", "[Indexing] 3/7: UNDER CONSTRUCTION"],
]
_POINT_SETTLED = [
    ["Point", ["id", "pointKind"], {"id": ["RANGE"], "pointKind": ["RANGE"]},
     None, "english", [], "NODE", "OPERATIONAL"],
]
#: `test (g)` the moment after its two `DROP` statements return and BEFORE the
#: engine's indexer thread applies them: idle, no rebuild status, and both
#: retired definitions still named. Quiescence alone treats this as settled.
_POINT_BEFORE_THE_DROPS_APPLIED = [
    ["Point", ["id", "pointKind"], {"id": ["RANGE"], "pointKind": ["RANGE"]},
     None, "english", [], "NODE", "OPERATIONAL"],
    ["Point", ["content", "search_keys"],
     {"content": ["FULLTEXT"], "search_keys": ["FULLTEXT"]},
     None, "english", [], "NODE", "OPERATIONAL"],
]
#: The same label once BOTH drops are applied: `pointKind` and the legacy
#: two-field fulltext index are gone — what `test (g)`'s guards require.
_POINT_AFTER_THE_DROPS_APPLIED = [
    ["Point", ["id"], {"id": ["RANGE"]},
     None, "english", [], "NODE", "OPERATIONAL"],
]


class _ScriptedProj:
    """A projection whose catalogue answers from a script, one read per query.

    The last entry repeats, so a wait that never settles keeps reading the
    final state instead of raising IndexError — the shape a real hung rebuild
    presents.
    """

    def __init__(self, *answers):
        self._answers = list(answers)
        self.reads = 0
        self.g = SimpleNamespace(query=self._query)

    def _query(self, _cypher):
        index = min(self.reads, len(self._answers) - 1)
        self.reads += 1
        return SimpleNamespace(result_set=self._answers[index])


def test_a_settled_catalogue_costs_exactly_one_read():
    """The steady state — every pass in the suite — must not poll.

    A helper that always slept first would add 0.1 s to every caller and hide a
    contract violation behind a sleep; one read is the whole cost of "settled".
    """
    proj = _ScriptedProj(_OBJECT_WITHOUT_FULLTEXT)
    assert index_rows_after_settle(proj) == _OBJECT_WITHOUT_FULLTEXT
    assert proj.reads == 1


def test_a_rebuild_in_flight_is_waited_out():
    """`UNDER CONSTRUCTION` anywhere in a row means the catalogue is mid-rebuild.

    This is the `test (g)` mechanism: the retired row is still there, so a read
    taken now would report the drop as failed.
    """
    proj = _ScriptedProj(_POINT_REBUILDING, _POINT_SETTLED)
    assert index_rows_after_settle(proj) == _POINT_SETTLED
    assert proj.reads == 2


def test_a_stale_pre_drop_row_is_waited_out_not_returned():
    """The `test (b)` shape: NOT every un-applied drop shows `UNDER CONSTRUCTION`.

    A catalogue read taken before the engine applied the drop is internally
    consistent — no rebuild status, the idle `OPERATIONAL` one — so quiescence
    ALONE returns it and the caller asserts about a drop that has not happened
    yet. `until` is what makes that read wait.
    """
    proj = _ScriptedProj(_OBJECT_WITH_FULLTEXT, _OBJECT_WITHOUT_FULLTEXT)
    rows = index_rows_after_settle(
        proj, until=lambda r: index_kind_absent(r, "Object", "name", "FULLTEXT"))
    assert rows == _OBJECT_WITHOUT_FULLTEXT
    assert proj.reads == 2


def test_a_bound_that_expires_returns_the_LAST_read_never_a_tolerant_pass():
    """The fail-closed half: never settle on a state the caller did not ask for.

    On expiry the callers' own assertions run against the mid-flight catalogue
    and RED — a drop that genuinely cannot happen is still reported as failed
    rather than passing vacuously. `timeout=0` bounds it here; the shipped
    default is a runaway guard, not an expected wait.
    """
    proj = _ScriptedProj(_OBJECT_WITH_FULLTEXT)
    rows = index_rows_after_settle(
        proj,
        until=lambda r: index_kind_absent(r, "Object", "name", "FULLTEXT"),
        timeout=0)
    assert rows == _OBJECT_WITH_FULLTEXT, (
        "an expired bound must hand back the unsettled read so the caller's "
        "assertion fires — returning [] or a fabricated clean state would turn "
        "a real failure into a silent pass")
    assert INDEX_SETTLE_TIMEOUT_S > 1.0, (
        "the shipped bound is a runaway guard for a loaded runner, not the "
        "millisecond window measured idle")


def test_point_index_drops_visible_requires_BOTH_of_test_g_drops():
    """The `test (g)` precondition, both halves (#10807).

    One predicate, not two ad-hoc lambdas: the wait and the two vacuity guards
    below it must agree about what "the drops are visible" means, or the wait
    can settle on a catalogue the guards reject — the false red, restored.
    """
    assert point_index_drops_visible(_POINT_BEFORE_THE_DROPS_APPLIED) is False
    # Either drop still visible keeps the fixture waiting.
    assert point_index_drops_visible([_POINT_SETTLED[0]]) is False, (
        "the range drop alone must not certify")
    assert point_index_drops_visible([_POINT_BEFORE_THE_DROPS_APPLIED[1]]) is False, (
        "the fulltext drop alone must not certify")
    assert point_index_drops_visible(_POINT_AFTER_THE_DROPS_APPLIED) is True
    # A catalogue that names neither index cannot hide either of them.
    assert point_index_drops_visible([]) is True


def test_the_test_g_wait_closes_the_pre_drop_window_quiescence_misses():
    """The finding this pins: `test (g)`'s window is the one with NO status.

    When the `DROP` has returned but the indexer thread has not picked it up, the
    catalogue is internally consistent — pre-drop definition, no
    `UNDER CONSTRUCTION` — so a quiescence-only wait returns it on the FIRST
    read and the fixture then refuses a drop that landed. Armed with its own
    precondition, the same read is waited out.
    """
    stale = _ScriptedProj(_POINT_BEFORE_THE_DROPS_APPLIED,
                          _POINT_AFTER_THE_DROPS_APPLIED)
    assert index_rows_after_settle(stale) == _POINT_BEFORE_THE_DROPS_APPLIED, (
        "this is the defect, not the fix: without `until` the pre-drop read is "
        "returned immediately and `test (g)`'s guards red on a LANDED drop")
    assert stale.reads == 1

    armed = _ScriptedProj(_POINT_BEFORE_THE_DROPS_APPLIED,
                          _POINT_AFTER_THE_DROPS_APPLIED)
    rows = index_rows_after_settle(armed, until=point_index_drops_visible)
    assert rows == _POINT_AFTER_THE_DROPS_APPLIED
    assert armed.reads == 2


def test_the_test_g_call_site_actually_passes_its_precondition():
    """The contract is a fix only where it is USED (#10807).

    `test (g)` is the call site that lost the race, so a wait called there
    WITHOUT `until` re-armed the false red even though the helper supported the
    predicate. Pinned on the source text because the defect is a timing window:
    no fake can prove which call the fixture actually makes.
    """
    source = Path(__file__).with_name("test_9079_handle_write_authority.py").read_text()
    start = source.index("def test_schema_ddl_survives_the_armed_guard(")
    body = source[start:]
    end = body.find("\ndef ")
    body = body if end == -1 else body[:end]
    assert re.search(r"index_rows_after_settle\(\s*proj\s*,\s*until=", body), (
        "the `test (g)` call site must arm `index_rows_after_settle` with "
        "`point_index_drops_visible`; without it a catalogue read taken before "
        "the engine applied the drop is returned unwaited and its two vacuity "
        "guards refuse a drop that landed")


def test_index_kind_absent_reads_the_kinds_map_and_fails_closed():
    """The predicate itself, including the shapes that must NOT certify a drop."""
    assert index_kind_absent(_OBJECT_WITH_FULLTEXT, "Object", "name", "FULLTEXT") is False
    assert index_kind_absent(_OBJECT_WITHOUT_FULLTEXT, "Object", "name", "FULLTEXT") is True
    # A row for ANOTHER label says nothing about this one.
    assert index_kind_absent(_POINT_SETTLED, "Object", "name", "FULLTEXT") is True
    # A kinds map that is absent (or not a map) cannot prove the drop.
    assert index_kind_absent([["Object", ["name"], None]], "Object", "name",
                             "FULLTEXT") is False
    assert index_kind_absent([["Object"]], "Object", "name", "FULLTEXT") is False
    # The engine has spelled a single kind as a bare string; accept both.
    assert index_kind_absent(
        [["Object", ["name"], {"name": "FULLTEXT"}]], "Object", "name",
        "FULLTEXT") is False
    # Case is the engine's to choose; the comparison is kind, not spelling.
    assert index_kind_absent(
        [["Object", ["name"], {"name": ["fulltext"]}]], "Object", "name",
        "FULLTEXT") is False


def test_index_cells_flattens_every_row():
    """`test (g)`'s assertion searches the cells, so the flattening is a contract.

    One string per COLUMN, not per leaf: the dict/list columns stringify whole, and
    that is load-bearing — `not any("pointKind" in c for c in before)` finds the
    retired field inside the stringified kinds map, which a leaf-wise flatten would
    also do but a per-row flatten would not.
    """
    assert index_cells([["Object", ["id"], {"name": ["FULLTEXT"]}]]) == {
        "Object", "['id']", "{'name': ['FULLTEXT']}"}
    assert index_cells([]) == set()
