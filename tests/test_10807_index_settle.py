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

import ast
from pathlib import Path
from types import SimpleNamespace

from tests._index_settle import (
    INDEX_SETTLE_TIMEOUT_S,
    index_cells,
    index_kind_absent,
    index_rows_after_settle,
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


def test_every_consumer_passes_the_caller_precondition():
    """The module's contract, applied at BOTH call sites — not just one (#10807).

    Quiescence alone is not sufficient: a read taken before the engine applied
    the drop is internally consistent (no `UNDER CONSTRUCTION`, the idle
    `OPERATIONAL` status, the pre-drop definition), so it is returned on the
    FIRST read and the caller asserts about a drop that has not happened yet.
    That was review Finding 1 at `8a3324949`: `test (b)` passed an `until` and
    `test (g)` did not, so the false red this module exists to remove could
    still fire in the write-authority suite.

    pinned STRUCTURALLY because the defect is invisible behaviourally on an idle
    lane (`test (b)`'s window measures 3 ms) and only reds under CI load — the
    same reason this whole file exists. This module is exempt: it drives the raw
    wait on purpose, including quiescence-only.
    """
    missing: dict[str, list[int]] = {}
    call_sites: dict[str, list[int]] = {}
    for path in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        source = path.read_bytes()
        # Cheap pre-filter: parsing all ~1,200 test modules to find two call
        # sites would cost the suite seconds for nothing.
        if b"index_rows_after_settle" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name)
                    and func.id == "index_rows_after_settle"):
                continue
            call_sites.setdefault(path.name, []).append(node.lineno)
            if not any(kw.arg == "until" for kw in node.keywords):
                missing.setdefault(path.name, []).append(node.lineno)
    assert len(call_sites) >= 2, (
        "the scan must find the two call sites this module documents — fewer "
        f"means the pin went vacuous (a rename?): {call_sites}")
    assert not missing, (
        "these `index_rows_after_settle` calls pass no `until`, so a stale "
        "pre-drop read (idle, no `UNDER CONSTRUCTION`) is returned on the first "
        f"read and the caller's guard can refuse a GOOD fixture: {missing}")


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
