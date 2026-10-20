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
3. **The live caller writes the journal's OWN position — never an invented
   one.** `EventLog.append` reports the record's position (#9190, landed on main
   in `b3b4c2fb1`), and layer 1 (`public.journal_event`, exposed as
   `public.record_event_stream`) is written from it. So the live door must carry
   the ordinal through (`seq=`) and pass `position=seq is not None`, EVERY
   production call to that door must actually PASS an ordinal (a body that uses
   a `seq` nobody supplies certifies nothing — `seq` defaults to `None`, which
   is the silent `position=False` this gate exists to catch), while every OTHER
   production call to a `position`-taking entry point must still pass the
   literal `position=False` — a number IT invents collides with a later replay's
   numbering on `(org_id, graph_id, seq)` in silence.

EXIT CODES — a signal that cannot be read is not a pass.

    0  wired: a production caller + a writer for every layer + the journal's own
       position on the live path and no invented one anywhere else
    1  NOT wired: no production caller, a layer with no writer, an invented
       position, or a live door that drops the ordinal
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
DECLARED_GAPS: dict[str, str] = {
    "journal_event": (
        "layer 1's relational truth set. Migration 20261012000001 defines "
        "layer 1 AS `public.journal_event` and 20261012000004 exposes it as "
        "`public.record_event_stream`. It is WRITTEN — by the fold's POSITION "
        "leg, from the ordinal `EventLog.append` reports (#9190), which the "
        "live door now carries through `write_live_record(seq=…)`. It has no "
        "`RECORD_MUTATIONS` entry because it is not a record TYPE mutation: it "
        "is the per-record index the layer rows are keyed from, so the writer "
        "derivation above cannot see it and this declaration is where it is "
        "named instead of being dropped from `LAYERS`."
    ),
}

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

#: The names that take a `position` parameter, so a call that omits
#: `position=False` writes an invented `journal_event.seq`. `replay`/
#: `replay_plan` are what the parameter actually lives on
#: (`record_projection.py`), `write_records`/`write` are the raw seam spellings,
#: and `write_live_record` is NOT here: it takes no `position` parameter — it
#: takes the ORDINAL (`seq`) and derives the flag internally
#: (`position=seq is not None`), which is the point of a single door.
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


def _record_calls(tree: ast.AST) -> set[str]:
    """Calls reached THROUGH the record module (never a bare name collision)."""
    modules, functions = _record_bindings(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            owner, name = _resolve_record_call(node, modules, functions)
            if owner is not None and name in _CALL_NAMES:
                found.add(name)
    return found


def find_production_callers() -> tuple[list[dict[str, object]], list[str]]:
    """Modules in the package that import AND call the record's write half."""
    callers: list[dict[str, object]] = []
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
        if not (_imports_the_record(_imported_names(tree))
                or any(_record_bindings(tree))):
            continue
        called = sorted(_record_calls(tree))
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


#: Where the LIVE door lives, stated once so the rule and its injectable seam
#: cannot disagree about the module they are talking about.
_LIVE_DOOR_REL = "tortoise/record_writer.py"


def live_door_writes_the_journals_position(
        source: str | None = None) -> list[str]:
    """The LIVE door must write layer 1 from the append's ordinal (#9190).

    `EventLog.append` reports the record's journal position, and that number IS
    `journal_event.seq` — the replay numbers records ``enumerate(records,
    start=1)`` over the same read, so a live write and a full replay give one
    record one number by construction. The door therefore has to (a) TAKE the
    ordinal (`seq`) and (b) bind `position` to `seq is not None`, never to a
    literal `False` (the pre-#9190 deferral, which left layer 1 empty on every
    live write) and never to a literal `True` (which would invent a number for
    a log that reported none — the collision #9190 exists to remove).

    Two ways to fail, both checked: the door is missing (the wiring moved and
    this tool must learn the new place), or its `write_records(...)` call does
    not bind `position` to the ordinal-derived expression.

    ``source`` is the INJECTION SEAM, and it exists because a rule that can
    only be run against the real tree is a rule whose firing cannot be proven:
    the tree complies, so every assertion about it stays green when this
    function's body is replaced by ``return []`` — the exact defect the sibling
    ``position_problems_for_module`` was given a synthetic tree to close, moved
    one level down (#9307 review F1). Defaults to reading the real module, so
    ``check()`` and the CLI keep the one call shape.
    """
    if source is None:
        try:
            source = (REPO_ROOT / _LIVE_DOOR_REL).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return [f"{_LIVE_DOOR_REL} could not be read ({exc})"]
    try:
        tree = ast.parse(source, filename=_LIVE_DOOR_REL)
    except SyntaxError as exc:
        return [f"{_LIVE_DOOR_REL} could not be parsed ({exc})"]
    door = next(
        (node for node in ast.walk(tree)
         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
         and node.name == _LIVE_DOOR),
        None,
    )
    if door is None:
        return [
            f"{_LIVE_DOOR_REL} defines no {_LIVE_DOOR}() — the live door "
            "moved, and this check no longer knows where the position rule "
            "lives"
        ]
    problems: list[str] = []
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
        if not _is_position_from_ordinal(value):
            problems.append(
                f"{_LIVE_DOOR}() calls {_call_name(call)}(…) with "
                f"position={ast.unparse(value) if value is not None else '<unset>'}"
                " — the live path must WRITE layer 1 from the journal's own "
                "position (#9190), so bind `position=seq is not None`. A literal "
                "`False` is the pre-#9190 deferral (layer 1 stays empty on every "
                "live write); a literal `True` invents a number for a log that "
                "reported none and collides with a later replay's numbering"
            )
        first_seq = bound.get("first_seq")
        if not _references_ordinal(first_seq):
            problems.append(
                f"{_LIVE_DOOR}() calls {_call_name(call)}(…) with "
                f"first_seq={ast.unparse(first_seq) if first_seq is not None else '<unset>'}"
                " — the ordinal the append reported (#9190) must be the number "
                "the row is keyed on, not a default and not an expression "
                "that merely mentions it"
            )
    return problems


def _is_position_from_ordinal(value: ast.expr | None) -> bool:
    """Is this `position=` bound to `seq is not None` (the #9190 form)?"""
    return isinstance(value, ast.Compare) and ast.unparse(value) == "seq is not None"


#: The `first_seq=` forms that genuinely USE the append's ordinal. An
#: allow-list rather than a walk, because "mentions `seq`" is not "uses it":
#: `first_seq=1 if seq is not None else 1` mentions the ordinal, passes a
#: predicate built on `any(Name(id="seq"))`, and keys EVERY position row at
#: seq 1 — one row for a whole journal under `ON CONFLICT DO NOTHING`, i.e. the
#: "layer 1 is effectively empty" defect this whole rule exists to catch
#: (#9307 review P2). Add a form here when an honest one appears; the omission
#: is a false RED (noisy), never a false green (silent).
_ORDINAL_EXPRESSION_FORMS = frozenset({"seq", "seq if seq is not None else 1"})


def _references_ordinal(value: ast.expr | None) -> bool:
    """Does this expression USE the append's ordinal as the row's number?

    Deliberately not "is a bare Name": the honest spelling may be a conditional
    (`first_seq=seq if seq is not None else 1` — `write_records` needs a number
    even when the flag is off), and rejecting that shape would push a lane
    toward a literal default, which is the defect. What must be true is that the
    row's `seq` IS the ordinal, not merely that the expression mentions it.
    """
    if value is None:
        return False
    return ast.unparse(value) in _ORDINAL_EXPRESSION_FORMS


def position_problems_for_module(tree: ast.AST, rel: str) -> list[str]:
    """Every `position`-taking record call in ONE module must bind `False`.

    The walker below feeds this the real tree; the tests feed it synthetic
    modules, which is the only way to prove the rule FIRES on a spelling that
    does not exist yet (asserting `position_problems == []` on the real tree
    only proves the rule is SATISFIED — a rule mutated to `return []` stayed
    green, #9231 review P2).
    """
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
        if not (isinstance(value, ast.Constant) and value.value is False):
            problems.append(
                f"{rel}:{node.lineno} calls {name}(…) with "
                f"position={ast.unparse(value) if value is not None else '<unset>'}"
                " — a production caller that invents its own `seq` collides "
                "with a later replay's numbering (#9190), so it must pass the "
                "literal `position=False`. The LIVE door is the exception, and "
                "`live_door_writes_the_journals_position` holds it to the "
                "ordinal the journal reported"
            )
    return problems


def production_callers_defer_the_position() -> list[str]:
    """EVERY production call to a `position`-taking record entry point must pass
    `position=False` as a LITERAL.

    `live_door_writes_the_journals_position` reads only the body of the live
    door,
    so a FOURTH door that calls the seam directly — `write_records([...])`,
    `replay([...], conn)`, `RecordWriter().write([...])`, each of whose signature
    default is `position=True` — satisfies claim 1 (a production caller exists),
    is never visited by claim 3, and writes an invented `journal_event.seq` that
    collides with a later replay's numbering on `(org_id, graph_id, seq)` in
    silence (#9190; #9231 review P2). `replay` is named explicitly because it is
    the function the parameter actually lives on: a door spelled
    `record_projection.replay(...)` was invisible to BOTH checks.

    `write_live_record` is NOT held to this: it takes no `position` parameter —
    it takes the ORDINAL (`seq`) and derives the flag internally, which is the
    point of a single door (and what lets layer 1 be written on the live path).
    """
    problems: list[str] = []
    for path in _iter_package_modules():
        if path.stem in _RECORD_STEMS:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue  # `find_production_callers` already reports this
        problems.extend(position_problems_for_module(tree, rel))
    return problems


def live_call_sites_pass_the_ordinal() -> list[str]:
    """Every package call to the LIVE door must carry the append's ordinal.

    Claim 3 above parses the door's BODY, so it certifies that the door USES a
    `seq` it was given — and says nothing about whether anyone GIVES it one.
    `write_live_record(record)` is valid Python (`seq: int | None = None`), and
    it silently takes `position=False`, writes no position row, and leaves layer
    1 empty on live writes: the exact #9190 defect this gate exists to catch,
    with the gate still printing WIRED (#9307 review F2). Dropping the argument
    at one of the three doors, or adding a fourth door without it, is therefore
    invisible today and must not be.

    So the ORDINAL'S PROVENANCE is checked at every call site: the argument must
    be a name that is either (a) a REQUIRED parameter of the enclosing function
    (a door may forward the ordinal, but then an omission upstream is a
    `TypeError`, not a silent `None`) or (b) assigned in the same scope from a
    journal `append(...)` call (the append IS the ordinal's only source).
    """
    problems: list[str] = []
    for path in _iter_package_modules():
        if path.stem in _RECORD_STEMS:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue  # `find_production_callers` already reports this
        problems.extend(ordinal_argument_problems(tree, rel))
    return problems


def ordinal_argument_problems(tree: ast.AST, rel: str) -> list[str]:
    """The per-module half of :func:`live_call_sites_pass_the_ordinal`.

    Synthetic-module shaped (like :func:`position_problems_for_module`), because
    a rule that is only ever run against a complying tree cannot be proven to
    fire.
    """
    modules, functions = _record_bindings(tree)
    problems: list[str] = []
    for scope, call in _calls_by_scope(tree):
        owner, name = _resolve_record_call(call, modules, functions)
        if owner is None or name != _LIVE_DOOR:
            continue
        bound = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        value = bound.get("seq")
        where = f"{rel}:{call.lineno}"
        if value is None:
            problems.append(
                f"{where} calls {_LIVE_DOOR}() without `seq=` — the ordinal "
                "defaults to None, so the write silently takes "
                "`position=False` and layer 1 stays empty on a live write "
                "(#9190). Pass the append's own ordinal: `seq=<the value "
                "the journal append returned>`"
            )
            continue
        if not isinstance(value, ast.Name):
            problems.append(
                f"{where} calls {_LIVE_DOOR}() with "
                f"seq={ast.unparse(value)} — an expression the journal never "
                "reported. The ordinal is the append's return value, never a "
                "number this code computes (#9190)"
            )
            continue
        origin, detail = _name_origin(scope, value.id)
        if origin == "required-parameter":
            continue
        if origin == "appended":
            continue
        if origin == "optional-parameter":
            problems.append(
                f"{where} calls {_LIVE_DOOR}(…, seq={value.id}) where "
                f"`{value.id}` is a parameter of {_function_name(scope)}() "
                "WITH A DEFAULT — one caller omitting it becomes a silent "
                "`seq=None` and layer 1 stays empty (#9190). Make the "
                "parameter required, so the omission is a TypeError"
            )
        elif origin == "other":
            problems.append(
                f"{where} calls {_LIVE_DOOR}(…, seq={value.id}) where "
                f"`{value.id}` is assigned in {_function_name(scope)}() from "
                f"{detail}, not from a journal append — the position row would "
                "be numbered by something other than the journal (#9190)"
            )
        else:
            problems.append(
                f"{where} calls {_LIVE_DOOR}(…, seq={value.id}) where "
                f"`{value.id}` is neither an append result nor a required "
                f"parameter of {_function_name(scope)}() — this check cannot "
                "see where the ordinal came from, and an unreadable source is "
                "not a pass (#9190)"
            )
    return problems


#: The one spelling that PRODUCES a journal ordinal. `EventLog.append` returns
#: the record's position (#9190); nothing else may stand in for it.
_APPEND_NAME = "append"


def _function_name(scope: ast.AST) -> str:
    return getattr(scope, "name", "<module>")


#: The names a journal is bound under where a record is appended — `log` in
#: `sdk.py`, `self.log` in `api.py`, `api.log` in `mining.py`.
#:
#: The RECEIVER has to be a journal, not merely something with an `append`: a
#: list's `append` returns `None`, which the door maps to `position=False`, i.e.
#: layer 1 silently empty with no error, no counter and a `WIRED` verdict (#9307
#: review I1). Widening this list is a false RED (noisy) when an honest door
#: binds its journal under a new name; leaving it as "any `.append`" is a false
#: GREEN (silent), which is the direction this gate may not fail in.
_JOURNAL_RECEIVERS = frozenset({"log", "journal", "event_log"})


def _is_journal_append(value: ast.expr) -> bool:
    """Is this `<journal>.append(…)` — the ONE call that yields an ordinal?

    Name-only matching accepted `self._pending.append(rec)` (a list, returns
    `None`) and so certified a door that writes no position row at all (#9307
    review I1). The receiver is matched on its FINAL name, so all three shipped
    spellings — `log.append(…)`, `self.log.append(…)`, `api.log.append(…)` —
    resolve to a journal.
    """
    if not (isinstance(value, ast.Call) and _call_name(value) == _APPEND_NAME):
        return False
    func = value.func
    if not isinstance(func, ast.Attribute):
        return False  # a bare `append(…)` names no journal to have appended to
    receiver = func.value
    if isinstance(receiver, ast.Name):
        return receiver.id in _JOURNAL_RECEIVERS
    if isinstance(receiver, ast.Attribute):
        return receiver.attr in _JOURNAL_RECEIVERS
    return False


def _assignments_in(scope: ast.AST, name: str) -> list[ast.expr]:
    """Every value assigned to `name` in `scope`'s OWN body.

    Scoped rather than `ast.walk(scope)`, for the reason `_calls_by_scope`
    exists: `ast.walk` enters a NESTED function, so a helper that happens to do
    `seq = log.append(record)` made an outer `seq = self._counter + 1` read as
    "the append's result" — and the gate printed `WIRED` for a door that
    computes its own number (#9307 review I1).
    """
    found: list[ast.expr] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda)):
                continue  # a nested scope's bindings are not this one's
            if isinstance(child, ast.Assign):
                value, targets = child.value, child.targets
            elif isinstance(child, (ast.AnnAssign, ast.NamedExpr)):
                value, targets = child.value, [child.target]
            else:
                value, targets = None, []
            if value is not None and any(
                    isinstance(t, ast.Name) and t.id == name
                    for t in targets):
                found.append(value)
            visit(child)

    visit(scope)
    return found


def _calls_by_scope(tree: ast.AST) -> list[tuple[ast.AST, ast.Call]]:
    """`(enclosing scope, call)` for every call, per SCOPE.

    `ast.walk` cannot answer this: it enters a nested function and still
    reports the outer one as the enclosing scope, which would let a helper
    inside a door inherit the door's `seq`.
    """
    found: list[tuple[ast.AST, ast.Call]] = []

    def visit(node: ast.AST, scope: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda)):
                visit(child, child)
                continue
            if isinstance(child, ast.Call):
                found.append((scope, child))
            visit(child, scope)

    visit(tree, tree)
    return found


def _parameters(scope: ast.AST) -> dict[str, ast.expr | None]:
    """`{parameter: default-or-None}` for one function signature."""
    args = getattr(scope, "args", None)
    if not isinstance(args, ast.arguments):
        return {}
    positional = [*getattr(args, "posonlyargs", []), *args.args]
    defaults: list[ast.expr | None] = [None] * (
        len(positional) - len(args.defaults)) + list(args.defaults)
    params: dict[str, ast.expr | None] = {
        arg.arg: default
        for arg, default in zip(positional, defaults, strict=True)}
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        params[arg.arg] = default
    return params


def _name_origin(scope: ast.AST, name: str) -> tuple[str, str]:
    """Where `name` comes from in `scope`: `(origin, detail)`.

    Origins: `required-parameter` · `optional-parameter` · `appended` ·
    `other` (assigned here, but not from an append) · `unknown`.

    `appended` needs EVERY binding of the name in this scope to be a JOURNAL
    append: one append among other assignments does not prove which value
    reached the call, and a single non-journal `.append`/computed value is a
    number the journal never reported. Over-strict is a false RED (noisy);
    under-strict is a silent empty layer 1 (#9307 review I1).
    """
    params = _parameters(scope)
    if name in params:
        default = params[name]
        return (("required-parameter", "") if default is None
                else ("optional-parameter", ast.unparse(default)))
    assigned = _assignments_in(scope, name)
    if assigned and all(_is_journal_append(value) for value in assigned):
        return "appended", ""
    if assigned:
        return "other", ", ".join(sorted({ast.unparse(v) for v in assigned}))
    return "unknown", ""


def check() -> tuple[int, dict[str, object]]:
    """The whole gate: read the tree, import the fold, return `(exit code, report)`.

    NOT memoised (it was documented as memoised once, which was false — the
    docstring described a cache that did not exist, #9231 review P2). A caller
    that needs it twice should hold the result: the import dominates the cost
    and `sys.path` is not appended twice, but a verdict tool that silently
    re-reads a tree it just measured is a stale answer waiting to happen, so no
    cache is offered.
    """
    callers, parse_problems = find_production_callers()
    writers, fold_problems = layer_writers()

    gaps = []
    declared_gaps: list[dict[str, str]] = []
    for layer, tables in LAYERS.items():
        for table in tables:
            if writers.get(table):
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
    position_problems = (live_door_writes_the_journals_position()
                         + live_call_sites_pass_the_ordinal()
                         + production_callers_defer_the_position())

    report: dict[str, object] = {
        "production_callers": callers,
        "layer_writers": {t: sorted(set(types)) for t, types in writers.items()},
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
        written = [table for table in tables if writers.get(table)]
        if written:
            detail = ", ".join(
                f"{table} ({len(writers[table])} record types)" for table in written
            )
            print(f"  ✓ {layer:12s}: {detail}")
        else:
            print(f"  ✗ {layer:12s}: no writer for any of {list(tables)}")
    for problem in report["position_problems"]:
        print(f"  ✗ position: {problem}")
    for gap in report.get("declared_gaps", []):
        print(f"  ⚠ {gap['layer']:12s}: {gap['table']} — DECLARED GAP: {gap['deferral']}")
    if report.get("reason"):
        print(f"  reason: {report['reason']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
