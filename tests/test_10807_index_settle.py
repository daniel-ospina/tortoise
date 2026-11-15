"""The catalogue-settle wait the fixtures share (#10807) — pinned without a server.

WHY THIS FILE EXISTS
--------------------
`tests/_index_settle.index_rows_after_settle` is the ONE definition of "wait until
the FalkorDB index catalogue has SETTLED", shared by the fixtures that each lost
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
tolerant pass — the property the callers' unchanged assertions depend on.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from tests import _index_settle as _settle
from tests._index_settle import (
    INDEX_SETTLE_TIMEOUT_S,
    index_cells,
    index_kind_absent,
    index_kind_present,
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


def test_index_kind_present_reads_the_kinds_map_and_fails_closed():
    """The positive predicate's own shapes, INCLUDING the ones that must not
    certify a create.

    Mirror of `test_index_kind_absent_reads_the_kinds_map_and_fails_closed`:
    `index_kind_present` gates a wait that RELEASES an assertion, so a predicate
    that answered True on a row it cannot read would let the caller assert about
    a create the engine has not applied. Added at #12856, where the predicate
    became a consumer-facing requirement rather than a local convenience.
    """
    assert index_kind_present(_OBJECT_WITH_FULLTEXT, "Object", "name",
                              "FULLTEXT") is True
    assert index_kind_present(_OBJECT_WITHOUT_FULLTEXT, "Object", "name",
                              "FULLTEXT") is False
    # A row for ANOTHER label says nothing about this one.
    assert index_kind_present(_OBJECT_WITH_FULLTEXT, "Point", "name",
                              "FULLTEXT") is False
    # …including a label that CONTAINS this one: the match is the label, not a
    # substring of it (`ObjectMeta`'s row must not answer for `Object`).
    assert index_kind_present([["ObjectMeta", ["name"], {"name": ["FULLTEXT"]}]],
                              "Object", "name", "FULLTEXT") is False
    # …and it is not case-folded either: a label is the engine's exact spelling.
    assert index_kind_present([["object", ["name"], {"name": ["FULLTEXT"]}]],
                              "Object", "name", "FULLTEXT") is False
    # A kinds map that is absent (or not a map) cannot prove the create.
    assert index_kind_present([["Object", ["name"], None]], "Object", "name",
                              "FULLTEXT") is False
    assert index_kind_present([["Object"]], "Object", "name", "FULLTEXT") is False
    # The field present under a DIFFERENT kind is not this kind.
    assert index_kind_present([["Object", ["name"], {"name": ["RANGE"]}]],
                              "Object", "name", "FULLTEXT") is False
    # The engine has spelled a single kind as a bare string; accept both.
    assert index_kind_present(
        [["Object", ["name"], {"name": "FULLTEXT"}]], "Object", "name",
        "FULLTEXT") is True
    # Case is the engine's to choose; the comparison is kind, not spelling.
    assert index_kind_present(
        [["Object", ["name"], {"name": ["fulltext"]}]], "Object", "name",
        "FULLTEXT") is True


def _foldable_bool(node) -> tuple[bool, bool | None]:
    """Fold a CONSTANT-BOOLEAN expression: ``(True, value)`` when it is decidable.

    One step per node, no evaluation of anything that reads data: literals,
    ``not``, and ``and``/``or`` chains of already-foldable parts. This is what
    lets the arming rule be an ALLOW-LIST without blessing `lambda r: not
    False`, `lambda r: True or False`, or a name bound to either.
    """
    if isinstance(node, ast.Constant):
        return True, bool(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        ok, value = _foldable_bool(node.operand)
        return (True, not value) if ok else (False, None)
    if isinstance(node, ast.BoolOp):
        parts = [_foldable_bool(v) for v in node.values]
        if not all(ok for ok, _ in parts):
            return False, None
        values = [v for _, v in parts]
        return True, (all(values) if isinstance(node.op, ast.And) else any(values))
    return False, None


def _until_is_armed(call, ctx=None) -> bool:
    """Does this `index_rows_after_settle(...)` call pass a USABLE `until`?

    ⛔ A PRESENT KEYWORD IS NOT AN ARMED WAIT, and neither is any single spelling
    of a constant: `until=None` is the helper's own default — the quiescence-only
    read this pin exists to forbid — so a syntactic `kw.arg == "until"` test
    certified the exact defect it names. The same hole was then re-measured one
    spelling at a time (#12856 review cycles 6-9): `until=None`, `NONE = None`
    … `until=NONE`, `until=lambda r: True`, `until=bool`, `until=all`,
    `until=lambda r: not False`, `until=lambda r: True or False`, a module-level
    `ALWAYS = lambda r: True`, and `def _ALWAYS(rows): return True` — each
    certified GREEN while returning on the FIRST catalogue read. Enumerating
    spells is a deny-list, and a deny-list only ever covers the spellings
    somebody thought of.

    ⛔ SO THE RULE IS AN ALLOW-LIST: a call is armed only when the scan can show
    the value is a PREDICATE OVER ROWS.
      * a lambda whose body is not constant-foldable → armed (it reads `rows`);
      * a name imported from `tests._index_settle` → armed (the documented
        predicates), or a module-level `def` whose body is not a constant
        `return`, or a module-level name bound to an armed-shaped value;
      * a module-qualified `_index_settle.<predicate>` → armed.
    Everything else — a bare builtin (`bool`, `any`), a truthy constant, or a
    name the scan cannot resolve — is UNARMED, so the pin REDS and asks for a
    declaration instead of certifying a read it cannot vouch for.

    ⛔ THE RESIDUAL, STATED SO IT CANNOT BE MISTAKEN FOR COVERAGE: a predicate
    reached through indirection OUTSIDE this file is out of reach — imported from
    another test module (`from tests.test_x import _always as P; until=P`), or
    rebound inside the function. Those stay armed and are the honest limit of an
    AST pin; the `**{"until": None}` splat is reported UNARMED (it reds the pin
    rather than passing it).
    """
    ctx = ctx or {}
    aliases = ctx.get("aliases", {})
    predicate_names = ctx.get("predicate_names", frozenset())
    settle_names = ctx.get("settle_names", frozenset())
    for kw in call.keywords:
        if kw.arg != "until":
            continue
        value = kw.value
        if isinstance(value, ast.Lambda):
            if not (value.args.args or value.args.posonlyargs
                    or value.args.kwonlyargs):
                return False  # not a predicate over rows at all
            ok, constant = _foldable_bool(value.body)
            return not constant if ok else True
        if isinstance(value, ast.Constant):
            return value.value is not None  # `until=True` raises at runtime: loud
        if isinstance(value, ast.Name):
            return (value.id in predicate_names or value.id in settle_names
                    or aliases.get(value.id) in settle_names)
        if isinstance(value, ast.Attribute):
            return value.attr in settle_names
        return True
    return False


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
        # The documented predicates, by their public names — the module under
        # test is imported by this pin, so `dir()` IS the list. Used for a
        # module-qualified `_index_settle.<name>` and for a name imported from it.
        settle_names = {n for n in dir(_settle) if not n.startswith("_")}
        # The ALLOW-LIST evidence for `_until_is_armed`, gathered from THIS
        # file: which names are predicates over rows. A module-level `def` whose
        # body is not a constant `return`, plus a module-level name bound to a
        # lambda that reads its argument (or to another armed name), plus every
        # public name of `tests._index_settle` (the documented predicates that a
        # module-qualified `_index_settle.<name>` names).
        predicate_names: set[str] = set()
        assigned: dict[str, ast.expr] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = [s for s in node.body
                        if not (isinstance(s, ast.Expr)
                                and isinstance(s.value, ast.Constant))]
                constant_return = (
                    len(body) == 1 and isinstance(body[0], ast.Return)
                    and isinstance(body[0].value, ast.Constant))
                if not constant_return:
                    predicate_names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned[target.id] = node.value
            elif (isinstance(node, ast.AnnAssign)
                  and isinstance(node.target, ast.Name) and node.value is not None):
                assigned[node.target.id] = node.value
        for name, value in assigned.items():
            if isinstance(value, ast.Lambda):
                if not (value.args.args or value.args.posonlyargs
                        or value.args.kwonlyargs):
                    continue
                ok, constant = _foldable_bool(value.body)
                if not ok or not constant:
                    predicate_names.add(name)
            elif (isinstance(value, ast.Name)
                  and (value.id in predicate_names or value.id in settle_names)):
                predicate_names.add(name)
        ctx = {"predicate_names": predicate_names,
               "settle_names": settle_names, "aliases": aliases}
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
            if not _until_is_armed(node, ctx):
                missing.setdefault(path.name, []).append(node.lineno)
    return call_sites, missing


def _settle_site_functions(paths) -> dict[str, dict[str, int]]:
    """How many armed-ish settle calls does each FUNCTION of each module carry?

    The MODULE key is too coarse a floor: a module that keeps ONE armed call
    still satisfies it after every other was deleted. Measured at #12856's review
    — deleting both waits inside the test that red is named for left the module
    key green. A SET of functions is too coarse as well: removing ONE of a
    documented function's two waits left it green (measured at review cycle 7).
    Counting the call sites per function is what makes a single deletion visible.

    Nested functions and class METHODS are attributed to their enclosing name
    (``Class.method``; a nested function's sites also count for its parent,
    because the parent's body contains them), because `test_search_engine.py`
    arms its waits in methods.
    """
    out: dict[str, dict[str, int]] = {}

    def _record(path_name: str, lines, node, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                end = child.end_lineno or child.lineno
                # ⛔ COUNT CALLS, NOT DISTINCT LINES: two waits written on one
                # line are two armed calls, and a `set` of line numbers would
                # report one — so deleting the second stayed green (measured at
                # #12856's review cycle 8).
                count = sum(1 for line in lines
                            if child.lineno <= line <= end)
                if count:
                    out.setdefault(path_name, {})[name] = count
                _record(path_name, lines, child, name + ".")
            elif isinstance(child, ast.ClassDef):
                _record(path_name, lines, child, prefix + child.name + ".")

    for path in sorted(Path(p) for p in paths):
        source = path.read_bytes()
        if b"index_rows_after_settle" not in source:
            continue
        call_sites, _missing = _settle_call_sites([path])
        lines = call_sites.get(path.name, [])
        if not lines:
            continue
        _record(path.name, lines, ast.parse(source), "")
    return out


def test_every_consumer_passes_the_caller_precondition():
    """The module's contract, applied at EVERY documented call site (#10807).

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

    ⛔ THE FLOOR NAMES (MODULE, FUNCTION) PAIRS — not a count, and not a module
    (review of #10807, then of #12856): `len(call_sites) >= 2` could be satisfied
    by two NEW call sites, and a MODULE key could be satisfied by one surviving
    armed call while the documented one was deleted. Measured at #12856's review:
    removing BOTH waits inside `test_event_id_index_is_required_so_an_existing_
    graph_gets_it` left the module key green.

    ⛔ AND IT IS SET EQUALITY OVER THE DOCUMENTED MODULES, not `declared ⊆ armed`.
    A one-way check protects the pairs somebody remembered to list: measured at
    #12856's review cycle 5, FIVE of the TEN armed functions in these four
    modules were undeclared, so each could have lost its wait with this pin
    green — the same vacuity the pair granularity was added to close, one level
    down. Declaring a site is now part of arming a wait in a documented module.

    ⛔ AND EACH PAIR DECLARES ITS CALL COUNT, not merely its existence: with a
    SET of pairs, SIX of the TEN declared pairs carried TWO waits, so removing
    one of the two — the DROP, keeping its recreate — left the pin green
    (measured at review cycle 7). The count is the granularity the waits have.
    """
    here = Path(__file__).resolve()
    paths = [p for p in here.parent.glob("test_*.py") if p.name != here.name]
    call_sites, missing = _settle_call_sites(paths)
    required = set(_settle._DOCUMENTED_CONSUMERS)
    assert required <= set(call_sites), (
        "the scan must find the call site in each module this file documents — a "
        f"missing one means the pin went vacuous (a rename?): {sorted(call_sites)}")
    by_function = _settle_site_functions(paths)
    have = {(mod, fn): n for mod, fns in by_function.items()
            for fn, n in fns.items()}
    declared = dict(_settle._DOCUMENTED_SITES)
    assert declared.keys() <= have.keys(), (
        "these documented (module, function) pairs no longer arm a settle — "
        "removing the wait re-opens the pre-drop race the pair exists to close: "
        f"{sorted(declared.keys() - have.keys())}")
    drifted = {pair: {"declared": count, "armed": have.get(pair)}
               for pair, count in declared.items()
               if have.get(pair) != count}
    assert not drifted, (
        "these documented (module, function) pairs carry a different number of "
        "armed settle calls than this pin declares — a wait was removed from (or "
        "added to) one of them, which re-opens the pre-drop race the pair exists "
        f"to close: {drifted}")
    undeclared = _undeclared_sites(
        by_function, _settle._DOCUMENTED_CONSUMERS, declared)
    assert not undeclared, (
        "these (module, function) pairs arm a settle in a module this pin "
        "DOCUMENTS but are absent from `_DOCUMENTED_SITES` — an undeclared wait "
        "is one the floor cannot protect, so removing it later would leave the "
        f"pin green: add them to `_DOCUMENTED_SITES`: {sorted(undeclared)}")
    assert not missing, (
        "these `index_rows_after_settle` calls pass no usable `until` (absent, or "
        "the literal `until=None`, which IS the quiescence-only default), so a "
        "stale pre-drop read (idle, no `UNDER CONSTRUCTION`) is returned on the "
        f"first read and the caller's guard can refuse a GOOD fixture: {missing}")


def _undeclared_sites(by_function, documented_modules,
                      declared) -> set[tuple[str, str]]:
    """Armed settle sites a DOCUMENTED module has that ``declared`` omits.

    The second half of the floor, kept as its own function so
    `test_the_floor_reds_on_an_armed_wait_it_does_not_declare` can drive it on
    synthetic input: an arm nobody has SEEN fire is indistinguishable from one
    that cannot fire. ``by_function`` is module -> {function: armed count} (a
    plain set of functions also works — iterating it yields the names), and
    ``declared`` is any iterable of pairs, so a count ledger and a set ledger are
    both accepted.

    Restricted to ``documented_modules`` on purpose — a module this pin does not
    document may arm a wait freely; only the modules it claims to speak for owe
    a declaration.
    """
    armed = {(mod, fn) for mod, fns in by_function.items() for fn in fns}
    return {site for site in armed if site[0] in set(documented_modules)} \
        - set(declared)


def test_the_floor_reds_on_an_armed_wait_it_does_not_declare():
    """Both directions of the SET-EQUALITY arm, on synthetic scans.

    ⛔ Pre-fix the floor was `declared ⊆ armed` only, so an armed wait missing
    from `_DOCUMENTED_SITES` was invisible: measured at #12856's review cycle 5,
    FIVE of the TEN armed functions in the four documented modules were
    undeclared and every one of them could have lost its wait with this pin
    green. The clean case is asserted too, so the arm cannot be a constant-red.
    """
    documented = {"test_indexes.py"}
    declared = {("test_indexes.py", "test_declared")}

    # A documented module with an armed wait nobody declared -> reported.
    assert _undeclared_sites(
        {"test_indexes.py": {"test_declared", "test_undeclared"}},
        documented, declared) == {("test_indexes.py", "test_undeclared")}

    # Declared AND armed -> clean (the non-vacuity control).
    assert _undeclared_sites(
        {"test_indexes.py": {"test_declared"}}, documented, declared) == set()

    # A module this pin does NOT document may arm a wait freely.
    assert _undeclared_sites(
        {"test_other.py": {"test_anything"}}, documented, declared) == set()

    # A declared site that stopped arming is the OTHER arm's finding, not this one.
    assert _undeclared_sites(
        {"test_indexes.py": set()}, documented, declared) == set()


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

    ⛔ `test_g` is the #12856 shape: `until=None` PRESENTS the keyword while
    asking for the quiescence-only read, so it must land in `missing` (measured
    at #12856's review cycle 6: this exact edit left every check green).

    ⛔ `test_h` is the same defect ONE NAME AWAY — `NONE = None` then
    `until=NONE`; a pin that only reads literals certifies it (measured at
    review cycle 7). `test_i`/`test_j` are the PREDICATE form: a truthy constant
    body (`lambda r: True`) returns on the first read and must be reported
    missing, while a falsy one waits out the deadline and reds the caller
    (fail-closed) and stays armed (measured at review cycle 8).
    """
    mod = tmp_path / "test_synthetic_consumer.py"
    mod.write_text(
        "import tests._index_settle as _index_settle\n"
        "from tests._index_settle import index_rows_after_settle as settle\n"
        "from tests._index_settle import point_index_drops_visible as visible\n"
        "NONE = None\n"
        "def test_a(proj):\n"
        "    _index_settle.index_rows_after_settle(proj)\n"
        "def test_b(proj):\n"
        "    _index_settle.index_rows_after_settle(proj, until=visible)\n"
        "def test_c(proj):\n"
        "    index_rows_after_settle(proj, until=visible)\n"
        "def test_d(proj):\n"
        "    settle(proj)\n"
        "def test_e(proj):\n"
        "    settle(proj, until=visible)\n"
        "def test_f(proj):\n"
        "    visible(proj)\n"
        "def test_g(proj):\n"
        "    index_rows_after_settle(proj, until=None)\n"
        "def test_h(proj):\n"
        "    index_rows_after_settle(proj, until=NONE)\n"
        "def test_i(proj):\n"
        "    index_rows_after_settle(proj, until=lambda r: True)\n"
        "def test_j(proj):\n"
        "    index_rows_after_settle(proj, until=lambda r: False)\n",
        encoding="utf-8")
    call_sites, missing = _settle_call_sites([mod])
    assert call_sites == {
        "test_synthetic_consumer.py": [6, 8, 10, 12, 14, 18, 20, 22, 24]}, call_sites
    assert missing == {
        "test_synthetic_consumer.py": [6, 12, 18, 20, 22]}, missing


def test_the_arming_rule_is_an_allow_list_not_a_deny_list(tmp_path):
    """Every spelling review cycles 6-9 measured GREEN is now UNARMED.

    `until=bool`, `until=any`, `lambda r: not False`, `lambda r: True or False`,
    a module-level `ALWAYS = lambda r: True`, and `def _ALWAYS(rows): return True`
    each returned on the FIRST catalogue read (the quiescence-only defect) while
    a deny-list certified them armed — one new spelling per cycle. The rule is now
    an allow-list, so these are unarmed by construction rather than enumerated;
    the last case keeps it non-vacuous (a real predicate over `rows` is armed).
    """
    mod = tmp_path / "test_denylist_shapes.py"
    mod.write_text(
        "from tests._index_settle import index_rows_after_settle\n"
        "ALWAYS = lambda r: True\n"
        "def _ALWAYS(rows):\n"
        "    return True\n"
        "def test_builtin(proj):\n"
        "    index_rows_after_settle(proj, until=bool)\n"
        "def test_not_false(proj):\n"
        "    index_rows_after_settle(proj, until=lambda r: not False)\n"
        "def test_boolop(proj):\n"
        "    index_rows_after_settle(proj, until=lambda r: True or False)\n"
        "def test_named_lambda(proj):\n"
        "    index_rows_after_settle(proj, until=ALWAYS)\n"
        "def test_named_def(proj):\n"
        "    index_rows_after_settle(proj, until=_ALWAYS)\n"
        "def test_real_predicate(proj):\n"
        "    index_rows_after_settle(proj, until=lambda r: r[0])\n",
        encoding="utf-8")
    _call, missing = _settle_call_sites([mod])
    assert missing == {"test_denylist_shapes.py": [6, 8, 10, 12, 14]}, missing


def test_two_waits_on_one_line_are_counted_as_two(tmp_path):
    """The per-pair count must count CALLS, not distinct line numbers.

    A `set` of `call_sites` collapsed two waits written on one line into one, so
    deleting the second matched the declared count and the pin stayed green
    (measured at #12856's review cycle 8). Every current site is multi-line, so
    this shape is pinned synthetically rather than waited for.
    """
    mod = tmp_path / "test_synthetic_oneline.py"
    mod.write_text(
        "from tests._index_settle import index_rows_after_settle, "
        "point_index_drops_visible as visible\n"
        "def test_two(proj):\n"
        "    index_rows_after_settle(proj, until=visible); "
        "index_rows_after_settle(proj, until=visible)\n",
        encoding="utf-8")
    assert _settle_site_functions([mod]) == {
        "test_synthetic_oneline.py": {"test_two": 2}}


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
