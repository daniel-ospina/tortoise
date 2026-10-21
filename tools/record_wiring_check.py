#!/usr/bin/env python3
"""Is the Postgres record WIRED? (#9188 / #9078, epic #7879)

WHY THIS EXISTS. `tortoise/record_projection.py` was complete, proven and
callable — and **nothing in production called its write half**. Measured on
#9078: its only importer was `supabase/tests/pglite/validate.mjs`, a test
harness. That is not a defect in any one function, so no unit test could see
it: the claim "the record is written in production" was true of the tests and
false of the product, and only a check that asks the *shape of the code base*
can say so. This is that check.

WHAT IT ASSERTS (exit 0 only when all three hold)

1. **A production caller exists.** Some module inside the shipped package
   (`tortoise/`) imports the record's write half and CALLS it. "Production" is
   not a path allowlist a probe can join: it is *the package that is
   installed*, so `tests/**`, `docs/evidence/**` and `supabase/tests/**` can
   never satisfy it — the issue's own words: *"a probe is the defect, not the
   fix."*
2. **Every one of the four ontology layers has a writer.** The layer tables are
   derived from `record_projection.RECORD_MUTATIONS` — the ONE declaration of
   journal→record — never from a hand-written list here. A second list is the
   second declaration that goes stale.
3. **The live caller carries the record's OWN position.** `journal_event.seq`
   is a fact of the journal (#9190): only the number a record's own
   `EventLog.append` RETURNED may be written, and `None` (a named absence) means
   no row for that record. So the live door must TAKE the ordinal and must not
   bind a constant `position`: a literal `False` throws away an ordinal the
   append already returned (layer 1's relational truth set stays empty), and a
   literal `True` invents one. Both are FAILURES, and so is an append site that
   does not pass the append's own return.

EXIT CODES — a signal that cannot be read is not a pass.

    0  wired: a production caller + a writer for every layer + a position that is
       the append's own return
    1  NOT wired: no production caller, a layer with no writer, or a position
       invented or discarded
    2  CANNOT ANSWER: a module could not be parsed, or the fold could not be read

The tool is read-only and DB-free: it must run in every lane, including where
no Postgres exists.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # #4053: this is an ENVIRONMENT failure, not a wiring verdict. `SystemExit(<str>)`
    # exits 1, which is NOT WIRED — so a check that never ran would be read by this
    # tool's own consumer as "the record is not wired", the exact mis-attribution the
    # sibling tool (`collision_preflight.py`) documents. Emit the message on stderr and
    # exit 2, CANNOT ANSWER. The constant is defined below; this guard necessarily runs
    # before it (and before the imports, #5128), so the value is spelled out with the
    # name in this comment rather than referenced.
    print(
        f"record-wiring-check: requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/record_wiring_check.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)  # EXIT: CANNOT ANSWER — not 1 (NOT WIRED)

import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "tortoise"

#: The four ontology layers and the record tables each is made of. Layer 4
#: (procedural) owns NO table by construction — `20261012000004_record_
#: procedural.sql` is views over layers 1-3 ("The procedural layer is a
#: VIEW/READ — never a fourth table") — so the writer its `object_current_status`
#: view needs is the `object` row. Stated here because a maintainer looking for
#: a procedural TABLE must not find one.
LAYERS: dict[str, tuple[str, ...]] = {
    "event stream": ("event", "source", "session", "journal_event"),
    "semantic": ("object", "subject"),
    "epistemic": ("point", "relation"),
    "procedural": ("object",),
}

#: Tables a layer OWNS whose writer is a NAMED deferral rather than an
#: oversight. A table here is still REPORTED (it is never silently dropped from
#: `LAYERS`), but its absence does not fail the gate — the deferral is the
#: decision, and hiding it in a shorter table list is how a green gate came to
#: mean "layer 1 is written" while `public.record_event_stream` stayed empty
#: (#9231 review P2).
#:
#: EMPTY, and pinned empty: `journal_event` was the one entry, and it was
#: granted on the premise that the live path had no journal ordinal. #9190
#: landed the ordinal (`EventLog.append` returns it, #9190/#9216) and this lane
#: now passes it through, so the exemption is STALE — the gate's own staleness
#: check would fail on it (#9231 review P1). A new deferral needs a tracker in
#: its reason, and expires the day the writer lands.
DECLARED_GAPS: dict[str, str] = {}

#: The modules that ARE the record, never callers of it.
_RECORD_STEMS = frozenset({"record_projection", "record_writer"})

#: The names that mean "this file calls the record's write half" — and the
#: record module they must be reached THROUGH. A bare `replay`/`RecordWriter`
#: is not evidence: a module can hold a local helper of either name, or
#: construct a writer it never writes with. Only a binding that resolves to the
#: record module counts (via the module itself, an import alias, or a
#: `from … import`), so the check cannot go green on a name collision.
#: `replay` is here because `record_projection.replay` IS the write half — a
#: fourth door spelled that way writes record rows exactly as `write_records`
#: does (#9231 review P2).
_CALL_NAMES = frozenset({"write_live_record", "write_records", "write", "replay"})

#: The names that take a `position` parameter, so a call that binds a CONSTANT
#: writes a `journal_event.seq` from nowhere: `True` invents one, and `False`
#: throws away the ordinal the append already returned. `replay`/
#: `replay_plan` are what the parameter actually lives on
#: (`record_projection.py`), `write_records`/`write` are the raw seam spellings,
#: and `write_live_record` is NOT here: the position rule for the live door is
#: checked on its own (`live_door_carries_the_position`), which is stricter.
_POSITION_NAMES = frozenset({"write_records", "write", "replay", "replay_plan"})

#: The one LIVE door: the helper every journal-append site calls. Its existence
#: is what the position check anchors on.
_LIVE_DOOR = "write_live_record"


def _iter_package_modules() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if p.is_file())


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            for alias in node.names:
                names.add(alias.name)
    return names


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _imports_the_record(names: set[str]) -> bool:
    """Does an import set reach `record_projection`/`record_writer`?"""
    for name in names:
        head = name.split(".")[-1]
        if head in _RECORD_STEMS:
            return True
    return False


def _record_bindings(
        tree: ast.AST) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """`({local module name: record stem}, {local function: (stem, name)})`.

    A door is not always spelled `record_writer.write_records(...)`. The
    ordinary Python spellings — `from .record_writer import write_records` then
    a bare call, `import tortoise.record_writer as rw` then `rw.write_records`,
    `from . import record_writer as rw` — are the SAME production call, and an
    earlier version of this check saw none of them because it matched only
    `Name.attr` where the name was literally `record_writer` (#9231 review P2).
    Resolving each module's own import table closes that without a second list
    of module names.
    """
    modules: dict[str, str] = {}
    functions: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                stem = alias.name.split(".")[-1]
                if stem not in _RECORD_STEMS:
                    continue
                # `import tortoise.record_writer` binds `tortoise`, so the
                # spelling is `tortoise.record_writer.write_records(…)` —
                # resolved by the dotted-path branch below.
                local = alias.asname or (
                    alias.name.split(".")[0] if "." in alias.name
                    else alias.name)
                modules[local] = stem
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                stem = alias.name.split(".")[-1]
                if stem in _RECORD_STEMS:
                    # `from . import record_writer [as rw]`
                    modules[alias.asname or alias.name] = stem
                    continue
                owner = (node.module or "").split(".")[-1]
                if owner in _RECORD_STEMS:
                    # `from .record_writer import write_records [as wr]`
                    functions[alias.asname or alias.name] = (owner, alias.name)
    return modules, functions


def _dotted(node: ast.AST) -> str | None:
    """`a.b.c` for a pure attribute chain rooted at a Name, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _resolve_record_call(
        call: ast.Call,
        modules: dict[str, str],
        functions: dict[str, tuple[str, str]],
) -> tuple[str | None, str | None]:
    """`(record stem, called name)` when `call` reaches the record module.

    The base expression decides: a module binding (`record_writer`, an import
    alias, `tortoise.record_writer`), a function imported straight out of the
    module, or a constructed `RecordWriter()` whose `.write(…)` is the seam.
    """
    func = call.func
    if isinstance(func, ast.Name):
        return functions.get(func.id, (None, None))
    if not isinstance(func, ast.Attribute):
        return None, None
    name = func.attr
    base = func.value
    if isinstance(base, ast.Name) and base.id in modules:
        return modules[base.id], name
    if isinstance(base, ast.Name) and base.id in _RECORD_STEMS:
        return base.id, name
    dotted = _dotted(base)
    if dotted:
        head = dotted.split(".")[-1]
        if head in _RECORD_STEMS:
            return head, name
        if dotted in modules:
            return modules[dotted], name
    if isinstance(base, ast.Call):
        owner, _ctor = _resolve_record_call(base, modules, functions)
        if owner is not None:
            return owner, name          # `record_writer.RecordWriter().write(…)`
    return None, None


def _record_calls(tree: ast.AST,
                  modules: dict[str, str] | None = None,
                  functions: dict[str, tuple[str, str]] | None = None) -> set[str]:
    """Calls reached THROUGH the record module (never a bare name collision)."""
    if modules is None or functions is None:
        modules, functions = _record_bindings(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            owner, name = _resolve_record_call(node, modules, functions)
            if owner is not None and name in _CALL_NAMES:
                found.add(name)
    return found


#: `(rel path, parsed tree, module bindings, function bindings)` for one module.
Parsed = tuple[str, ast.AST, "dict[str, str]", "dict[str, tuple[str, str]]"]

#: A parsed package: every module, ONCE.
_ParsedPackage = tuple[list["Parsed"], list[str]]


def parse_package() -> _ParsedPackage:
    """Parse the whole package ONCE, with each module's record bindings.

    Three rules ask the same questions of the same trees, so `check()` builds
    this and hands it to all of them. Measured before that: three independent
    walks re-parsed and re-bound every module, and one verdict cost **67 s of
    AST traversal** — on a gate the tests and CI both run. Parsing a tree once
    per verdict is not a cache (which this tool deliberately does not offer, see
    `check()`): it is one measurement read by the rules that share it.
    """
    parsed: list[Parsed] = []
    problems: list[str] = []
    for path in _iter_package_modules():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if path.stem in _RECORD_STEMS:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            problems.append(f"{rel}: could not be parsed ({exc})")
            continue
        modules, functions = _record_bindings(tree)
        parsed.append((rel, tree, modules, functions))
    return parsed, problems


def _as_package(package: _ParsedPackage | None) -> _ParsedPackage:
    """`package` when a caller already parsed it, else a fresh parse."""
    return package if package is not None else parse_package()


def find_production_callers(
        package: _ParsedPackage | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    """Modules in the package that import AND call the record's write half."""
    callers: list[dict[str, object]] = []
    parsed, problems = _as_package(package)
    for rel, tree, modules, functions in parsed:
        if not (_imports_the_record(_imported_names(tree))
                or modules or functions):
            continue
        called = sorted(_record_calls(tree, modules, functions))
        if called:
            callers.append({"module": rel, "calls": called})
    return callers, problems


def layer_writers() -> tuple[dict[str, list[str]], list[str]]:
    """`{table: [record types]}` derived from the fold's own registry."""
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from tortoise import record_projection
    except Exception as exc:                          # unimportable fold
        return {}, [f"could not import tortoise.record_projection ({exc})"]
    registry = getattr(record_projection, "RECORD_MUTATIONS", None)
    if not registry:
        return {}, ["record_projection.RECORD_MUTATIONS is empty or missing"]
    writers: dict[str, list[str]] = {}
    problems: list[str] = []
    for record_type, spec in registry.items():
        mutations = spec if isinstance(spec, (list, tuple)) else (spec,)
        for mutation in mutations:
            tables, problem = _tables_of(mutation)
            if problem:
                problems.append(f"{record_type}: {problem}")
            for table in tables:
                writers.setdefault(table, []).append(record_type)
    return writers, problems


#: Effects whose table is IMPLICIT in the statement emitter (they carry no
#: `node`/`table` attribute), named here so the check sees every layer. A new
#: effect class that is missing from this map makes the check CANNOT-ANSWER
#: (exit 2) rather than quietly under-reporting a layer — the fail-closed
#: direction, and the reason this is a map and not a default.
_IMPLICIT_TABLES: dict[str, tuple[str, ...]] = {
    "RelationUpsert": ("relation",),
    "RelationDelete": ("relation",),
    # The refusal STAYS in the event stream: it writes no layer row by design
    # (NoRecordEffect, #9187/#9200 row 4).
    "NoRecordEffect": (),
}


def _tables_of(mutation: object) -> tuple[set[str], str | None]:
    """`({table}, None)` for a mutation, or `(set(), reason)` if unclassifiable.

    Structural first (any nested attribute carrying a `table`), then the
    declared implicit map. Anything else is a hole in this check, reported
    rather than assumed empty.
    """
    found: set[str] = set()
    effect = getattr(mutation, "effect", mutation)
    table = getattr(effect, "table", None)
    if isinstance(table, str) and table:
        found.add(table)
    for value in getattr(effect, "__dict__", {}).values():
        nested = getattr(value, "table", None)
        if isinstance(nested, str) and nested:
            found.add(nested)
    name = type(effect).__name__
    # `OpDispatch` (EntityMutated / PointsMerged) resolves its table from the
    # record's `op` via `table_for_label` — read the declaration rather than
    # guessing a layer.
    table_for_label = getattr(effect, "table_for_label", None)
    if isinstance(table_for_label, dict):
        found.update(t for t in table_for_label.values() if isinstance(t, str) and t)
    if name in _IMPLICIT_TABLES:
        found.update(_IMPLICIT_TABLES[name])
        return found, None
    if found:
        return found, None
    return set(), f"effect class {name!r} writes no table this check can see"


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """`{child: parent}` — needed to find the FUNCTION a call sits in."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing_function(node: ast.AST,
                        parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    scope = parents.get(node)
    while scope is not None:
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return scope
        scope = parents.get(scope)
    return None


def _is_append_call(node: ast.AST | None) -> bool:
    """Is `node` a call to something spelled `.append(...)` — the journal append?"""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
    )


def _names_in(node: ast.AST | None) -> set[str]:
    """Every bare name referenced by an expression."""
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)} if node is not None else set()


def _parameter_names(func: ast.AST) -> list[str]:
    """A function's parameters, in binding order (`args.args` + kwonly)."""
    args = func.args
    return [arg.arg for arg in args.posonlyargs + args.args + args.kwonlyargs]


def _argument_for(call: ast.Call, func: ast.AST,
                  name: str) -> ast.AST | None:
    """The expression `call` passes for the parameter `name` of `func`, if any."""
    ordered = func.args.posonlyargs + func.args.args
    keyword_only = {arg.arg for arg in func.args.kwonlyargs}
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    if name in keyword_only:
        return None
    index = [arg.arg for arg in ordered].index(name)
    return call.args[index] if index < len(call.args) else None


def _derives_from_append(node: ast.AST | None, tree: ast.AST,
                         parents: dict[ast.AST, ast.AST],
                         depth: int = 0) -> bool:
    """Does `node` carry a journal append's OWN returned position?

    Three shapes are accepted, and nothing else: the append call inline
    (`first_seq=log.append(event)`); a local bound from one
    (`seq = log.append(event)` … `first_seq=seq`) in the SAME function; or —
    for a door that hands the ordinal through a private helper, as
    `TortoiseSDK._emit_event` does with `_write_record_projection` — a PARAMETER
    of the enclosing function, which is then followed to that function's own
    call sites and traced there. The hop is bounded (`depth`) and every hop
    must still land on an `append(…)` return, so a parameter that is fed from
    anywhere else fails.
    """
    if _is_append_call(node):
        return True
    if depth > 3 or not isinstance(node, ast.Name):
        return False
    scope = _enclosing_function(node, parents)
    if scope is None:
        return False
    for sub in ast.walk(scope):
        if not isinstance(sub, (ast.Assign, ast.AnnAssign)):
            continue
        targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
        if not any(isinstance(t, ast.Name) and t.id == node.id for t in targets):
            continue
        if _is_append_call(sub.value):
            return True
    if node.id not in _parameter_names(scope):
        return False
    for sub in ast.walk(tree):
        if not isinstance(sub, ast.Call) or _call_name(sub) != scope.name:
            continue
        passed = _argument_for(sub, scope, node.id)
        if passed is not None and _derives_from_append(
                passed, tree, parents, depth + 1):
            return True
    return False


def _traces_to(node: ast.AST | None, name: str, scope: ast.AST | None) -> bool:
    """Does `node`'s value come from the parameter/local `name`?

    Accepts the parameter itself, an expression that REFERENCES it
    (`first_seq is not None`, `first_seq if ok else 1`), or a local bound from
    either of those inside `scope`. That is what keeps `position` from being a
    constant — the flag must be derived from the ordinal, not asserted.
    """
    if node is None:
        return False
    if isinstance(node, ast.Name):
        if node.id == name:
            return True
        if scope is None:
            return False
        for sub in ast.walk(scope):
            if not isinstance(sub, (ast.Assign, ast.AnnAssign)):
                continue
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            if not any(isinstance(t, ast.Name) and t.id == node.id for t in targets):
                continue
            if _names_in(sub.value) & {name}:
                return True
        return False
    return bool(_names_in(node) & {name})


def live_door_carries_the_position() -> list[str]:
    """The LIVE door must TAKE the ordinal and derive the flag from it (#9190).

    The live path's position is a fact only the record's own journal append
    holds (`EventLog.append` RETURNS it, #9190/#9216), so the door is the place
    that carries it from the append to the seam. Four ways to fail, each one a
    shape this door has actually had or could plausibly take:

    * the door is MISSING (the wiring moved and this tool must learn where);
    * it calls no seam entry point at all (nothing is written through the
      record module);
    * it takes no `first_seq` parameter — then the ordinal cannot reach it, and
      the position row is either invented or categorically deferred;
    * its `write_records(…)` call binds `position` to a CONSTANT (`False` is the
      categorical deferral #9190 removed: layer 1's `journal_event` /
      `record_event_stream` stays empty on every live write; `True` invents a
      `seq` that collides on `(org_id, graph_id, seq)` with a later replay's
      numbering), or to anything that does not trace back to `first_seq`.
    """
    path = REPO_ROOT / "tortoise" / "record_writer.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return [f"tortoise/record_writer.py could not be read ({exc})"]
    return door_carries_the_position_problems(tree, "tortoise/record_writer.py")


def door_carries_the_position_problems(tree: ast.AST,
                                       rel: str) -> list[str]:
    """The rule itself, on a tree — so the tests can feed it a WRONG door.

    Split from :func:`live_door_carries_the_position` for the reason
    :func:`position_problems_for_module` is split from its walker: a rule only
    ever run on a compliant tree cannot be told apart from `return []`
    (#9231 review P2).
    """
    door = next(
        (node for node in ast.walk(tree)
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
         and node.name == _LIVE_DOOR),
        None,
    )
    if door is None:
        return [
            f"{rel} defines no {_LIVE_DOOR}() — the live "
            "door moved, and this check no longer knows where the position "
            "rule lives"
        ]
    problems: list[str] = []
    arguments = door.args.posonlyargs + door.args.args + door.args.kwonlyargs
    if "first_seq" not in {arg.arg for arg in arguments}:
        problems.append(
            f"{_LIVE_DOOR}() takes no `first_seq` parameter, so the journal "
            "position its own append returned (#9190) cannot reach the record: "
            "the position row is then either invented or never written"
        )
    calls = [
        node for node in ast.walk(door)
        if isinstance(node, ast.Call) and _call_name(node) in _POSITION_NAMES
    ]
    if not calls:
        return [
            f"{_LIVE_DOOR}() calls no seam entry point — nothing is written "
            "through the record module"
        ]
    for call in calls:
        bound = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        value = bound.get("position")
        if isinstance(value, ast.Constant):
            problems.append(
                f"{_LIVE_DOOR}() calls {_call_name(call)}(…) with the CONSTANT "
                f"position={ast.unparse(value)} — the journal position is the "
                "record's own append's return (#9190), so a constant either "
                "throws that ordinal away (False) or invents one (True)"
            )
        elif not _traces_to(value, "first_seq", door):
            problems.append(
                f"{_LIVE_DOOR}() calls {_call_name(call)}(…) with "
                f"position={ast.unparse(value) if value is not None else '<unset>'}"
                " — it must be DERIVED from the `first_seq` the caller's journal "
                "append returned (`first_seq is not None`), never asserted"
            )
        ordinal = bound.get("first_seq")
        if not _traces_to(ordinal, "first_seq", door):
            problems.append(
                f"{_LIVE_DOOR}() calls {_call_name(call)}(…) with "
                f"first_seq={ast.unparse(ordinal) if ordinal is not None else '<unset>'}"
                " — the door must FORWARD the `first_seq` its caller supplied, "
                "never substitute a constant, a count, or a number minted here "
                "(#9190)"
            )
    return problems


def live_doors_pass_the_append_position(
        package: _ParsedPackage | None = None) -> list[str]:
    """Every production call to the live door must hand it the append's return.

    `live_door_carries_the_position` proves the SINGLE door forwards the
    ordinal; this proves each APPEND SITE actually gives it one. Without this
    half, a door that accepts `first_seq` and every caller that omits it are
    both "compliant" — the door would fall back to `None`, no
    `journal_event` row would ever be written, and the gate would still print
    WIRED (#9231 review P1: the deferral was pinned by a test and enforced by
    the gate, so the empty layer 1 was self-certifying).

    Each `write_live_record(…)` call must bind `first_seq` to a journal
    `.append(…)` call's own return — inline, or through a local bound from one
    in the same function.
    """
    problems: list[str] = []
    parsed, _ = _as_package(package)
    for rel, tree, modules, functions in parsed:
        problems.extend(live_door_call_problems(
            tree, rel, modules=modules, functions=functions))
    return problems


def live_door_call_problems(
        tree: ast.AST, rel: str,
        modules: dict[str, str] | None = None,
        functions: dict[str, tuple[str, str]] | None = None) -> list[str]:
    """Every `write_live_record(…)` call in ONE module must pass the ordinal."""
    problems: list[str] = []
    if modules is None or functions is None:
        modules, functions = _record_bindings(tree)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _resolve_record_call(node, modules, functions)[1] == _LIVE_DOOR
    ]
    if not calls:
        return problems
    # Only a module that HAS an append site pays for the parent map: building
    # one for all 239 modules dominated this check (measured, #9231 follow-up).
    parents = _parents(tree)
    for node in calls:
        bound = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        ordinal = bound.get("first_seq")
        if not _derives_from_append(ordinal, tree, parents):
            problems.append(
                f"{rel}:{node.lineno} calls {_LIVE_DOOR}(…) with "
                f"first_seq="
                f"{ast.unparse(ordinal) if ordinal is not None else '<unset>'}"
                " — the journal position is the record's OWN `append(…)` "
                "return (#9190); without it no `journal_event` row is "
                "written and layer 1 stays empty"
            )
    return problems


def position_problems_for_module(
        tree: ast.AST, rel: str,
        modules: dict[str, str] | None = None,
        functions: dict[str, tuple[str, str]] | None = None) -> list[str]:
    """No module may write the record with a CONSTANT `position` flag.

    The walker below feeds this the real tree; the tests feed it synthetic
    modules, which is the only way to prove the rule FIRES on a spelling that
    does not exist yet (asserting `position_problems == []` on the real tree
    only proves the rule is SATISFIED — a rule mutated to `return []` stayed
    green, #9231 review P2).

    `position=False` is accepted (it writes no row, which is honest); anything
    that is not a constant is accepted here because the live door's derivation
    is checked by :func:`live_door_carries_the_position` and the append sites by
    :func:`live_doors_pass_the_append_position`. What fails is `position=True`
    (an invented `seq`) and an OMITTED `position` (whose default is `True`) —
    both write a `journal_event` row that claims a position from nowhere.
    """
    if modules is None or functions is None:
        modules, functions = _record_bindings(tree)
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        owner, name = _resolve_record_call(node, modules, functions)
        if owner is None or name not in _POSITION_NAMES:
            continue
        bound = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        value = bound.get("position")
        if isinstance(value, ast.Constant) and value.value is False:
            continue
        if value is not None and not isinstance(value, ast.Constant):
            continue
        problems.append(
            f"{rel}:{node.lineno} calls {name}(…) with "
            f"position={ast.unparse(value) if value is not None else '<unset>'}"
            " — a live caller has no invented journal ordinal (#9190), so it"
            " must pass `position=False` or the position its own journal append"
            " returned"
        )
    return problems


def production_callers_invent_no_position(
        package: _ParsedPackage | None = None) -> list[str]:
    """EVERY production call to a `position`-taking record entry point must
    either write no position row or derive it from the append's own return.

    `live_door_carries_the_position` reads only the body of `write_live_record`,
    so a FOURTH door that calls the seam directly — `write_records([...])`,
    `replay([...], conn)`, `RecordWriter().write([...])`, each of whose signature
    default is `position=True` — satisfies claim 1 (a production caller exists),
    is never visited by claim 3, and writes an invented `journal_event.seq` that
    collides with a later replay's numbering on `(org_id, graph_id, seq)` in
    silence (#9190; #9231 review P2). `replay` is named explicitly because it is
    the function the parameter actually lives on: a door spelled
    `record_projection.replay(...)` was invisible to BOTH checks.
    """
    problems: list[str] = []
    parsed, _ = _as_package(package)
    for rel, tree, modules, functions in parsed:
        problems.extend(position_problems_for_module(
            tree, rel, modules=modules, functions=functions))
    return problems


#: The one emitter of a record's `journal_event` row lives in the fold
#: (`record_projection._journal_row`), keyed on `(org_id, graph_id, seq)`. The
#: TABLE it writes is read off that statement, never restated here — a second
#: declaration is the one that goes stale, and the reason this check exists at
#: all (#9231 review P1: layer 1's table was exempted from having a writer, so
#: an empty `public.record_event_stream` printed WIRED).
_JOURNAL_INSERT = "INSERT INTO public."


def position_row_tables() -> tuple[set[str], list[str]]:
    """`({table}, [])` the position row writes, or `(set(), [problem])`.

    Read from `record_projection._journal_row`'s own SQL, so the table this
    gate credits is the table the emitter actually inserts into. A rename or a
    removed emitter is reported instead of silently shrinking layer 1.
    """
    path = REPO_ROOT / "tortoise" / "record_projection.py"
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return set(), [f"tortoise/record_projection.py could not be read ({exc})"]
    row = next(
        (node for node in ast.walk(tree)
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
         and node.name == "_journal_row"),
        None,
    )
    if row is None:
        return set(), [
            "tortoise/record_projection.py defines no _journal_row() — layer "
            "1's position row has no emitter, so `public.journal_event` (and "
            "the `record_event_stream` view over it) is written by nothing"
        ]
    tables: set[str] = set()
    for node in ast.walk(row):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        for fragment in node.value.split(_JOURNAL_INSERT)[1:]:
            table = fragment.split()[0].strip("\"'(),")
            if table:
                tables.add(table.split(".")[-1])
    if not tables:
        return set(), [
            "record_projection._journal_row() inserts into no "
            f"`{_JOURNAL_INSERT}<table>` — the position rule credits a table "
            "this check cannot name"
        ]
    return tables, []


def check() -> tuple[int, dict[str, object]]:
    """The whole gate: read the tree, import the fold, return `(exit code, report)`.

    NOT memoised (it was documented as memoised once, which was false — the
    docstring described a cache that did not exist, #9231 review P2). A caller
    that needs it twice should hold the result: the import dominates the cost
    and `sys.path` is not appended twice, but a verdict tool that silently
    re-reads a tree it just measured is a stale answer waiting to happen, so no
    cache is offered.
    """
    package = parse_package()
    parse_problems = package[1]
    callers, _ = find_production_callers(package)
    writers, fold_problems = layer_writers()
    position_tables, position_row_problems = position_row_tables()
    fold_problems = fold_problems + position_row_problems

    gaps = []
    declared_gaps: list[dict[str, str]] = []
    for layer, tables in LAYERS.items():
        for table in tables:
            if writers.get(table) or table in position_tables:
                continue
            deferral = DECLARED_GAPS.get(table)
            if deferral:
                declared_gaps.append(
                    {"layer": layer, "table": table, "deferral": deferral})
            else:
                gaps.append(
                    f"{layer} layer has NO writer: {table} is not written by "
                    "any record type in RECORD_MUTATIONS"
                )
    # A STALE exemption is a FAILURE, not a permanent licence (#9231 review P2):
    # `DECLARED_GAPS` is the one lever that can turn this gate from exit 1 to
    # exit 0 (delete the sole failure a missing layer produces), so it must not
    # outlive the reason it was granted. The day `journal_event` gains a live
    # writer, #9190 is done and this entry has to go — silently keeping it would
    # hide the NEXT table that lost its writer.
    for table, deferral in DECLARED_GAPS.items():
        if writers.get(table):
            gaps.append(
                f"DECLARED_GAPS is STALE: {table} HAS a writer now "
                f"({', '.join(sorted(set(writers[table])))}), so the deferral "
                f"({deferral.splitlines()[0][:60]}…) must be removed — a "
                "standing exemption turns a future missing writer into a pass"
            )
    position_problems = (live_door_carries_the_position()
                         + live_doors_pass_the_append_position(package)
                         + production_callers_invent_no_position(package))

    report: dict[str, object] = {
        "production_callers": callers,
        "layer_writers": {t: sorted(set(types)) for t, types in writers.items()},
        "position_row_tables": sorted(position_tables),
        "gaps": gaps,
        "declared_gaps": declared_gaps,
        "parse_problems": parse_problems,
        "fold_problems": fold_problems,
        "position_problems": position_problems,
    }

    if parse_problems or fold_problems:
        report["verdict"] = "CANNOT ANSWER"
        return 2, report
    if not callers:
        report["verdict"] = "NOT WIRED"
        report["reason"] = (
            "no module inside tortoise/ imports AND calls the record's write "
            "half — the record has no production call site (#9078)"
        )
        return 1, report
    if gaps or position_problems:
        report["verdict"] = "NOT WIRED"
        return 1, report
    report["verdict"] = "WIRED"
    return 0, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)
    code, report = check()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return code

    callers = report["production_callers"]
    writers = report["layer_writers"]
    print(f"record wiring — {report['verdict']}")
    for key in ("parse_problems", "fold_problems"):
        for problem in report[key]:
            print(f"  ✗ unreadable: {problem}")
    if callers:
        for caller in callers:
            print(f"  ✓ caller: {caller['module']} → {', '.join(caller['calls'])}")
    else:
        print("  ✗ caller: none — no module in tortoise/ calls the write half")
    for layer, tables in LAYERS.items():
        written = [table for table in tables
                   if writers.get(table) or table in report["position_row_tables"]]
        if written:
            detail = ", ".join(
                f"{table} ({len(writers[table])} record types)"
                if writers.get(table) else f"{table} (position row)"
                for table in written
            )
            print(f"  ✓ {layer:12s}: {detail}")
        else:
            print(f"  ✗ {layer:12s}: no writer for any of {list(tables)}")
    for problem in report["position_problems"]:
        print(f"  ✗ position: {problem}")
    for gap in report["gaps"]:
        print(f"  \u2717 gap: {gap}")
    for gap in report.get("declared_gaps", []):
        print(f"  ⚠ {gap['layer']:12s}: {gap['table']} — DECLARED GAP: {gap['deferral']}")
    if report.get("reason"):
        print(f"  reason: {report['reason']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
