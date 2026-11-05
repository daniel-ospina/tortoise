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


def _settle_call_sites(paths) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Every `index_rows_after_settle(...)` call under `paths`: sites, and the
    ones missing their `until`.

    ⛔ BOTH CALL FORMS, because a module-prefixed call is invisible to a bare
    `ast.Name` scan (review of #10807). `index_rows_after_settle(proj)` and
    `_index_settle.index_rows_after_settle(proj)` are the SAME call — the form
    carries no meaning, only the arguments do — so a pin that reads one form stops
    covering the site it documents the day somebody writes the other.

    ⛔ AND A THIRD FORM: `from tests._index_settle import index_rows_after_settle as
    settle` then `settle(proj)` is the same call again, and it is neither `Name(
    id="index_rows_after_settle")` nor an attribute — an aliased import made the
    call INVISIBLE in both directions (no site recorded, so the floor could be met
    by unrelated sites, and no `until` check, so a pre-drop read passed the pin).
    Aliases are resolved from the module-level `ImportFrom` nodes rather than by
    matching the local name, so an alias of a DIFFERENT symbol from the same module
    is not mistaken for a call site.
    """
    missing: dict[str, list[int]] = {}
    call_sites: dict[str, list[int]] = {}
    for path in sorted(Path(p) for p in paths):
        source = path.read_bytes()
        # Cheap pre-filter: parsing all ~1,200 test modules to find two call
        # sites would cost the suite seconds for nothing.
        if b"index_rows_after_settle" not in source:
            continue
        tree = ast.parse(source)
        # local name -> the symbol it was imported as (`settle` ->
        # `index_rows_after_settle`), from any `from ._index_settle import …`.
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.endswith("_index_settle"):
                continue
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            named = (
                (isinstance(func, ast.Name)
                 and (func.id == "index_rows_after_settle"
                      or aliases.get(func.id) == "index_rows_after_settle"))
                or (isinstance(func, ast.Attribute)
                    and func.attr == "index_rows_after_settle")
            )
            if not named:
                continue
            call_sites.setdefault(path.name, []).append(node.lineno)
            if not any(kw.arg == "until" for kw in node.keywords):
                missing.setdefault(path.name, []).append(node.lineno)
    return call_sites, missing


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

    ⛔ THE FLOOR NAMES THE TWO MODULES, not a count (review of #10807):
    `len(call_sites) >= 2` could be satisfied by two NEW call sites while the two
    documented ones were renamed or dropped, so the pin could stay green over the
    exact regression it exists to catch.
    """
    here = Path(__file__).resolve()
    call_sites, missing = _settle_call_sites(
        p for p in here.parent.glob("test_*.py") if p.name != here.name)
    required = {"test_9079_handle_write_authority.py", "test_search_engine.py"}
    assert required <= set(call_sites), (
        "the scan must find the call site in each module this file documents — a "
        f"missing one means the pin went vacuous (a rename?): {sorted(call_sites)}")
    assert not missing, (
        "these `index_rows_after_settle` calls pass no `until`, so a stale "
        "pre-drop read (idle, no `UNDER CONSTRUCTION`) is returned on the first "
        f"read and the caller's guard can refuse a GOOD fixture: {missing}")


def test_the_scan_sees_a_module_prefixed_call_and_its_missing_until(tmp_path):
    """Both directions of the ATTR form, on a file whose lines are known exactly.

    ⛔ Pre-fix the scan matched bare `ast.Name` only, so `_index_settle.
    index_rows_after_settle(proj)` was invisible in BOTH directions: it never
    reached `call_sites` (so the floor could be met by unrelated sites) and it
    never reached `missing` (so a call that can return a pre-drop read passed the
    pin). Reverting to the bare-name test makes this case report `{}` for both.

    ⛔ `test_d`/`test_e` are the THIRD form — an ALIASED import. `settle(proj)` is
    the same call as `index_rows_after_settle(proj)`, and it was invisible to both
    the `Name(id=…)` and `Attribute(attr=…)` arms, so an alias was a free pass past
    the pin. `test_f` is the control that keeps the alias map from being a name
    match: importing a DIFFERENT symbol from the same module is not a call site.
    """
    mod = tmp_path / "test_synthetic_consumer.py"
    mod.write_text(
        "import tests._index_settle as _index_settle\n"
        "from tests._index_settle import index_rows_after_settle as settle\n"
        "from tests._index_settle import point_index_drops_visible as visible\n"
        "def test_a(proj):\n"
        "    _index_settle.index_rows_after_settle(proj)\n"
        "def test_b(proj):\n"
        "    _index_settle.index_rows_after_settle(proj, until=lambda rows: True)\n"
        "def test_c(proj):\n"
        "    index_rows_after_settle(proj, until=lambda rows: True)\n"
        "def test_d(proj):\n"
        "    settle(proj)\n"
        "def test_e(proj):\n"
        "    settle(proj, until=lambda rows: True)\n"
        "def test_f(proj):\n"
        "    visible(proj)\n",
        encoding="utf-8")
    call_sites, missing = _settle_call_sites([mod])
    assert call_sites == {"test_synthetic_consumer.py": [5, 7, 9, 11, 13]}, call_sites
    assert missing == {"test_synthetic_consumer.py": [5, 11]}, missing


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
