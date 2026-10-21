"""The call-time + empty-pattern census for `aboutObject` - derivation only, no pins.

Why this is a support module and not a test file: `#9398` landed the census with
these two derivations as its *tests*, pinned to the prose of its own table
(a row count and a `<from> -> <to> sites` change chain). `#9400` restructured
§13 - narration moved to the changelog, the table re-keyed on `module.py::symbol`
- so those prose pins cannot survive, while the DERIVATION they carried (a
relationship type supplied at CALL TIME, and the two spellings of an empty
relationship pattern) is exactly the part that must not be lost: it is the class
no name-based scan can see.

So the derivation lives here, verbatim from `#9398`'s guard, and
`tests/test_7948_about_object_read_sites.py` consumes it - one implementation,
two consumers, no second definition of what a reader is. Nothing here is a pin:
the pins (every derived site is a §13 row or a registered exclusion, and every
register entry still derives) belong to the guard that owns the table. The prose
regexes and the `test_*` arms of the original file are deliberately NOT here.
"""
from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TORTISE = ROOT / "tortoise"
STORAGE = ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"
EXTRACTOR = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"

#: The edge the §13 ruling is about.
EDGE = "aboutObject"

#: The Cypher clauses that can govern a relationship pattern. `MERGE`/`CREATE`
#: mint the edge, so they are writers; `MATCH`/`OPTIONAL MATCH` are the readers.
#: `re.IGNORECASE` is load-bearing, not decoration: Cypher keywords are
#: case-insensitive, so `match (n)-[:aboutObject]->(m)` is the same read as the
#: uppercase spelling. Without the flag such a reader derived in NO class — both
#: consumers `.upper()` a capture the regex could never lowercase, so the
#: normalization was dead code — and the completeness arm stayed green over it.
#: The trailing `\(` is load-bearing for the same reason and was measured, not
#: assumed: a clause in Cypher is always followed by a node pattern, while a prose
#: sentence is not. Case-insensitivity alone derived a FALSE site in
#: `value_extractor.GROUNDING_SYSTEM` — an LLM prompt whose prose says "match
#: summary items" and whose JSON schema then carries `[{"edge":
#: "aboutObject|…"}]`, so the English verb governed a bracket. A guard that reads
#: prose as Cypher is the same defect pointing the other way, and both
#: directions are pinned in `_MECHANISM_CASES`.
_CLAUSE = re.compile(r"\b(OPTIONAL\s+MATCH|MATCH|MERGE|CREATE)\b\s*\(", re.IGNORECASE)
#: A traversal: an opening `[` or `-` shortly before the relationship name, so
#: prose ("an ``aboutObject`` edge") is not mistaken for Cypher.
_TRAVERSAL = re.compile(rf"[-\[][^\n]{{0,140}}?{EDGE}", re.IGNORECASE)
#: A bracketed traversal, whatever its type slot holds (`-[r]->`, `<-[r]-`,
#: `-[r:aboutObject]->`, `-[r:{rel}]->`). `_type_slot` decides what the bracket
#: names; this only finds the brackets. The bracket-free spelling of the same
#: traversal is `_BARE_TRAVERSAL` — both must be walked, or the class is half-seen.
_EMPTY_TRAVERSAL = re.compile(r"[-<]\s*\[([^\]]*)\]\s*[-<>]")
#: The OTHER legal spelling of a relationship pattern that names no type: a
#: BRACKET-LESS traversal — `(n)--()`, `(n)-->(m)`, `(n)<--(m)`, `(n)<-->(m)`.
#: It is the SAME shape as `-[r]->`: no type is written, so it walks every
#: relationship type and `aboutObject` is one of them. The bracket detector above
#: cannot see it, and that was not academic — a live member existed while this
#: regex did not (`domain_validators.validate_chain_integrity`'s
#: `AND NOT (n)--()`), so §13's *"every such reader is enumerated by its own shape
#: walk"* was false at that head. Anchored between two node patterns (`)` … `(`)
#: on purpose: a bare `--` in prose, a CLI flag or a `->` in a log message is not
#: a traversal, and a Cypher relationship always sits between two nodes.
_BARE_TRAVERSAL = re.compile(r"(?<=\))\s*(?:<-->|<--|-->|--)\s*(?=\()")
#: Is the offset inside a Cypher PATTERN COMPREHENSION? The delimiter test is
#: exact rather than a heuristic: no other construct puts a relationship
#: bracket inside an unclosed `[` of the same query string — `[(n)-[r]->(m) |
#: r]`, `size([(n)-[r]->(m) | r])`. This is the predicate the retired guard's
#: literal arm pinned (`_reads_literally`), moved here so the guard's classifier
#: and this module's empty-pattern walk share ONE definition (#9493): a second
#: copy would be the two-implementations drift this repo keeps re-deriving.
def _inside_pattern_comprehension(query: str, start: int) -> bool:
    depth = 0
    for char in reversed(query[:start]):
        if char == "]":
            depth += 1
        elif char == "[":
            if depth == 0:
                return True
            depth -= 1
    return False
#: The text signature of a CALL-TIME type slot — a traversal whose relationship
#: type is written as an expression rather than a name: an f-string interpolation
#: inside the bracket (`-[r:{rel}]->`, `-[:{EDGE}]->`, including one that carries a
#: property map: `-[r:{rel} {confidence:$c}]->`), or a `+`-concatenated fragment
#: where the literal ENDS inside the pattern (`"MATCH (n)-[r:" + rel + "](m)"`).
#: Only a file FILTER (a file that cannot be parsed cheaply is not walked);
#: `_is_gap_type_read` decides, on the resolved candidate.
_GAP_TYPE_HINT = re.compile(
    r"[\[<][^\]\n]*:[^\]\n]*\{[^\]\n]*\]|[\[<][^\]\n]*:\s*[\"']"
)
#: The most candidate strings one expression may expand to. A rel list is at most
#: a handful of members, so this only bounds pathological input.
_MAX_CANDIDATES = 64
#: What an unresolvable interpolation contributes: a GAP, so the literal around
#: it still derives. Deliberately empty rather than a marker string — the point
#: is to keep the surrounding Cypher intact, not to guess the value.
_UNKNOWN = ""
#: A backticked token in §13's `site` column that can be a symbol.

#: ⚠️ The FLOORS of the retired guard's arms (`_MIN_SITES`, `_MIN_ROWS`,
#: `_MIN_CUSTOMER_CALLABLE`, `_MIN_CALL_TIME`, `_MIN_EMPTY_PATTERN`) are NOT here: a floor
#: was that guard's own guard-rail, its arms live in the guard that owns the table, and a
#: constant defined here would be a number nothing reads — the claim class this census
#: exists to stop being unread.
#: The empty-pattern readers that are NOT an `aboutObject` read for a referent,
#: declared by name with the reason, so a NEW member of the class cannot appear
#: silently: `test_the_empty_pattern_class_is_derived_and_declared` derives the class from
#: the pattern's shape and requires every member to be either a §13 row or an
#: entry here. Each entry is `(filename, symbol, why it is not an about read)`.
#:
#: ⚠️ **A site that owns a §13 ROW is not carried here (#9493).** A row says the
#: reader IS enumerated; an entry says the walk cannot observe the edge — both on
#: one site are opposite claims, and the stale one silently shields the other's
#: drift. The `#9400` remerge left ten such dead entries beside rows (the backup
#: family, the graph export, the delete preview, the replay, the v2.5 backfill,
#: the operator promotion and the hub-degree count — all rows since the third and
#: fifth passes); they are removed, and
#: `test_no_empty_pattern_entry_is_also_a_row` reds any regrowth.
EMPTY_PATTERN: tuple[tuple[str, str, str], ...] = (
    (
        "tortoise/domain_validators.py",
        "validate_chain_integrity",
        "the `orphaned_draft` rule's zero-edge check (`AND NOT (n)--()`) — a "
        "degree count over every edge type, used to decide whether a Point is "
        "orphaned; the same read `session_source_migration._degree` is registered "
        "for one file over. It asks nothing about a referent, and its bracket-less "
        "spelling is why the class walk had to cover both spellings: it was live "
        "and invisible while only the bracketed form derived",
    ),
    (
        "tortoise/hosted_api.py",
        "_load_commit_graph_state",
        "restore/rollback bookkeeping — it rewires every edge type it finds, "
        "`aboutObject` included, but asks nothing about a referent",
    ),
    (
        "tortoise/mining.py",
        "verify_no_auto_wire",
        "the no-auto-wire verifier walks every edge type to assert a shape is "
        "absent",
    ),
    (
        "tortoise/projection/entities.py",
        "_materialize_connector_source",
        "connector-source materialisation rewires the node's edges by type",
    ),
    (
        "tortoise/sdk.py",
        "_connected_pairs",
        "a connectivity inventory (which node pairs are joined at all) — the "
        "edge TYPE is deliberately ignored",
    ),
    (
        "tortoise/sdk.py",
        "provenance",
        "the provenance walk reports which edge types reach a Subject; an "
        "`aboutObject` edge is not a provenance link and is not classified as "
        "one",
    ),
    (
        "tortoise/session_source_migration.py",
        "_degree",
        "a degree count over every edge type, used to decide whether a Source "
        "is orphaned",
    ),
    (
        "tortoise/session_source_migration.py",
        "_delete_if_orphan",
        "the orphan sweep deletes every edge of a degree-0 Source",
    ),
    (
        "tortoise/session_source_migration.py",
        "_rel_types",
        "an inventory of the edge TYPES on a node, reported as labels",
    ),
)

#: The readers of the THIRD shape: a relationship type that is an EXPRESSION, not
#: a name — a function argument, a dict lookup, a journal field, a value imported
#: from a module this census does not parse. The resolver follows a name one level
#: deep and then leaves a GAP, so the resolved candidate carries an EMPTY type
#: slot (`-[r:]->`) and the shape walk (`_call_time_sites`) derives the class the
#: same way `_is_empty_pattern_read` derives the no-type one.
#:
#: ⚠️ This register is NOT the class. The class is derived; this is the list of
#: its members that are NOT an `aboutObject` read for a referent — gated to the
#: operator rels, a fixed non-about* edge, or a by-type edge rewrite. Each entry
#: is `(filename, symbol, why the gap cannot carry `aboutObject` for a referent)`,
#: and `test_the_call_time_shape_is_derived_and_declared` checks BOTH directions:
#: every derived member is a §13 row or an entry here (so a NEW reader of the
#: shape cannot ship green), and every entry still derives (so a stale entry
#: cannot make the next reader's absence invisible).
CALL_TIME_REL: tuple[tuple[str, str, str], ...] = (
    (
        "tortoise/analyze.py",
        "_bfs_select_operators",
        '`safe_rel`, a `for rel in rel_types` loop variable — the loop body skips '
        'every rel that is not `"IMPL"`/`"NAND"`, so the expansion queries cannot '
        "carry `aboutObject`; they walk the operator graph, not a referent",
    ),
    (
        "tortoise/commit_ops.py",
        "apply_payload_operators",
        '`_edge_type`, `"hasPart"` or the operator `op_type` — the `#4971` '
        "idempotency probe reads the exact edge `create_operator` would have "
        "written (operator → input), and the property map in the bracket "
        "(`[:{_edge_type} {idx:0}]`) is why the type slot stays a gap",
    ),
    (
        "tortoise/projection/__init__.py",
        "_capture_onboarding_snapshot",
        "`onboarding.state.COMPLETED_STEP_EDGE` (`\"COMPLETED_STEP\"`) — a fixed, "
        "non-about* step edge; imported INSIDE the function, and the defining "
        "module carries no edge literal, so the resolver cannot follow the "
        "binding and the slot stays a gap. It also does not derive the named "
        "census, because the value it resolves to contains no `aboutObject`",
    ),
    (
        "tortoise/projection/__init__.py",
        "_rebuild_all_replay",
        "`etype`, the `edge_type` field of a journaled replay event — the "
        "rebuild/rollback replay deletes and rewrites every replayed edge BY "
        "TYPE, so it carries the edge rather than reading it for a referent "
        "(registered for its empty patterns too: an edge can be replayed "
        "without its type being named)",
    ),
    (
        "tortoise/projection/entities.py",
        "_fold_edge_message_changed",
        '`rel`, from the event, gated to `("IMPL", "NAND")` before the query — '
        "an operator-edge attribute fold, not a referent read",
    ),
    (
        "tortoise/sdk.py",
        "_find_operator",
        '`edge_rel`, `"hasPart"` or the operator `op_type` — the dedupe probe '
        "collects an operator's targets, never a referent",
    ),
    (
        "tortoise/sdk.py",
        "create_direct_edge",
        "`rel_type`, `op_type` (IMPL / NAND, `create_operator`'s edge set) — the "
        "existence pre-count and attribute SET for an operator edge",
    ),
)


class _Index:
    """The string-constant bindings one module offers.

    Module level and per class, kept separately: `self._ABOUT_TYPES` is an
    attribute lookup, `SUPERSEDE_STRUCTURAL_RELS` a module global, and a
    resolver that merged the two namespaces would answer the wrong question for
    a class attribute shadowing a module name.
    """

    def __init__(self, tree: ast.Module):
        self.consts: dict[str, ast.AST] = {}
        self.classes: dict[str, dict[str, ast.AST]] = {}
        self._collect(tree.body, self.consts)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                attrs: dict[str, ast.AST] = {}
                self._collect(node.body, attrs)
                self.classes[node.name] = attrs

    @staticmethod
    def _collect(body: list[ast.stmt], into: dict[str, ast.AST]) -> None:
        for stmt in body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        into.setdefault(target.id, stmt.value)
            elif (
                isinstance(stmt, ast.AnnAssign)
                and stmt.value is not None
                and isinstance(stmt.target, ast.Name)
            ):
                into.setdefault(stmt.target.id, stmt.value)


class _File:
    """One parsed module: its tree, its parent map, its constant index."""

    def __init__(self, path: Path, rel: str, source: str):
        self.path = path
        self.rel = rel
        self.source = source
        self.tree = ast.parse(source)
        self.index = _Index(self.tree)
        self.parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        self._assigns: dict[int, dict[str, ast.AST]] = {}
        self._users: dict[str, list[str]] | None = None

    def own_assigns(self, fn: ast.AST) -> dict[str, ast.AST]:
        """A function's own assignments, resolved once per function."""
        key = id(fn)
        if key not in self._assigns:
            self._assigns[key] = _own_assigns(fn)
        return self._assigns[key]

    def const_users(self) -> dict[str, list[str]]:
        """`{name: [functions referencing it]}` — resolved once per module.

        The module-constant attribution below needs this for every constant
        literal; recomputing it per literal walked `sdk.py`'s 30k nodes several
        times over.
        """
        if self._users is None:
            users: dict[str, set[str]] = {}
            for node in ast.walk(self.tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Name):
                        users.setdefault(inner.id, set()).add(node.name)
            self._users = {name: sorted(found) for name, found in users.items()}
        return self._users


@lru_cache(maxsize=1)
def _sources() -> dict[Path, str]:
    """Every module under `tortoise/`, read once (text only — parsing is lazy)."""
    return {
        path: path.read_text(encoding="utf-8") for path in sorted(TORTISE.rglob("*.py"))
    }


@lru_cache(maxsize=1)
def _literal_files() -> tuple[_File, ...]:
    """The modules whose TEXT carries the edge name — the constant source.

    Any constant whose value contains the edge name has a literal for it in the
    module that DEFINES it (`SUPERSEDE_STRUCTURAL_RELS` in `sdk`, `ABOUT_EDGE_TYPES`
    in `ranking`), so this set is complete for the constant index below and 8x
    smaller than the package — parsing all 242 modules cost ~4 s of the suite's
    wall clock for nothing.
    """
    return tuple(
        _File(path, str(path.relative_to(ROOT)), source)
        for path, source in _sources().items()
        if EDGE in source
    )


@lru_cache(maxsize=1)
def _files() -> tuple[_File, ...]:
    """The modules the census walks: literal carriers, plus constant consumers.

    A module can build its rel pattern out of `SUPERSEDE_STRUCTURAL_RELS` without
    ever writing the literal, so the second half of this set is what keeps the
    census from reproducing its old blindness one level up. The third half is
    the empty-pattern class: a module that walks EVERY relationship type names
    neither the edge nor a constant, so it is found by the shape of its pattern
    (`_may_traverse_every_type`) — which is how `navigation.py` and the backfill
    census were missing from a census that had already declared the class. The
    fourth half is the call-time class, whose module may name neither the edge
    nor any type at all (`_may_have_gap_type`).
    """
    parsed = {file.path: file for file in _literal_files()}
    names = _rel_constant_names()
    for path, source in _sources().items():
        if path in parsed:
            continue
        if (
            any(name in source for name in names)
            or _may_traverse_every_type(source)
            or _may_have_gap_type(source)
        ):
            parsed[path] = _File(path, str(path.relative_to(ROOT)), source)
    return tuple(sorted(parsed.values(), key=lambda file: file.path))


@lru_cache(maxsize=1)
def _module_consts() -> dict[str, tuple[ast.AST, _File]]:
    """Module-level constants defined **exactly once** in the package.

    The census falls back to this index for a name it cannot bind in its own
    module — an `import`ed constant (`mcp_server` imports
    `SUPERSEDE_STRUCTURAL_RELS` from `sdk`). The uniqueness rule is what keeps
    the fallback from guessing between two different constants that happen to
    share a name: an ambiguous name resolves to nothing and is reported by the
    completeness arm as an undeclared empty-pattern site until it is declared.
    """
    seen: dict[str, list[tuple[ast.AST, _File]]] = {}
    for file in _literal_files():
        for name, value in file.index.consts.items():
            seen.setdefault(name, []).append((value, file))
    return {name: hits[0] for name, hits in seen.items() if len(hits) == 1}


@lru_cache(maxsize=1)
def _attr_consts() -> dict[str, tuple[ast.AST, _File]]:
    """Class-level constants defined exactly once (e.g. `_ABOUT_TYPES`)."""
    seen: dict[str, list[tuple[ast.AST, _File]]] = {}
    for file in _literal_files():
        for attrs in file.index.classes.values():
            for name, value in attrs.items():
                seen.setdefault(name, []).append((value, file))
    return {name: hits[0] for name, hits in seen.items() if len(hits) == 1}


def _own_assigns(fn: ast.AST) -> dict[str, ast.AST]:
    """Assignments made directly in a function body (nested scopes excluded)."""
    out: dict[str, ast.AST] = {}

    def visit(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        out.setdefault(target.id, stmt.value)
            elif (
                isinstance(stmt, ast.AnnAssign)
                and stmt.value is not None
                and isinstance(stmt.target, ast.Name)
            ):
                out.setdefault(stmt.target.id, stmt.value)
            for _field, value in ast.iter_fields(stmt):
                if isinstance(value, list) and value and all(
                    isinstance(item, ast.stmt) for item in value
                ):
                    visit(value)

    visit(fn.body)
    return out


def _param_defaults(fn: ast.AST) -> dict[str, ast.AST]:
    """A function's parameter DEFAULTS, by name.

    `link_entity(..., edge_type="aboutObject")` interpolates the *parameter*, not
    a local, and the default is where its value lives when every caller takes it
    — which is the case for the whole default capture path. Without this table
    the existence pre-probe that reads the edge on the write path is invisible
    even though the edge name is spelled one line above the query.
    """
    out: dict[str, ast.AST] = {}
    args = fn.args  # type: ignore[attr-defined]
    positional = list(args.posonlyargs) + list(args.args)
    paired = positional[len(positional) - len(args.defaults):]
    for arg, default in zip(paired, args.defaults, strict=True):
        out.setdefault(arg.arg, default)
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        if default is not None:
            out.setdefault(arg.arg, default)
    return out


def _scope(file: _File, node: ast.AST | None) -> dict[str, dict[str, ast.AST]]:
    """The bindings visible at `node`: loop vars, locals, params, attrs, globals.

    Innermost-first on every table: a loop variable shadows a local of the same
    name, which shadows a parameter default, which shadows the class attribute,
    which shadows the module global.
    """
    scope: dict[str, dict[str, ast.AST]] = {
        "loop": {},
        "local": {},
        "param": {},
        "cls": {},
        "module": file.index.consts,
    }
    if node is None:
        return scope
    seen_fn = False
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, ast.For) and isinstance(cur.target, ast.Name):
            scope["loop"].setdefault(cur.target.id, cur.iter)
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)) and not seen_fn:
            seen_fn = True
            for name, value in file.own_assigns(cur).items():
                scope["local"].setdefault(name, value)
            scope["param"] = _param_defaults(cur)
        if isinstance(cur, ast.ClassDef):
            scope["cls"] = {**file.index.classes.get(cur.name, {}), **scope["cls"]}
        cur = file.parents.get(cur)
    return scope


def _product(left: list[str], right: list[str]) -> list[str]:
    out: list[str] = []
    for head in left:
        for tail in right:
            out.append(head + tail)
            if len(out) >= _MAX_CANDIDATES:
                return out
    return out


def _resolve(
    node: ast.AST,
    scope: dict[str, dict[str, ast.AST]],
    file: _File,
    depth: int = 0,
) -> list[str]:
    """Every concrete string this expression can evaluate to.

    Unresolvable leaves contribute `_UNKNOWN` (a gap) rather than nothing, so a
    query that interpolates a value this resolver cannot follow still derives
    from the Cypher around it — the alternative (dropping the whole candidate)
    is exactly the literal-only blindness this function exists to remove.
    """
    if depth > 8:
        return [_UNKNOWN]
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else [_UNKNOWN]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out: list[str] = []
        for element in node.elts:
            out.extend(_resolve(element, scope, file, depth + 1))
        return out[:_MAX_CANDIDATES]
    if isinstance(node, ast.JoinedStr):
        candidates = [""]
        for part in node.values:
            inner = part.value if isinstance(part, ast.FormattedValue) else part
            candidates = _product(candidates, _resolve(inner, scope, file, depth + 1))
        return candidates
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _product(
            _resolve(node.left, scope, file, depth + 1),
            _resolve(node.right, scope, file, depth + 1),
        )
    if isinstance(node, ast.Name):
        if node.id in scope["loop"]:
            return _resolve(scope["loop"][node.id], scope, file, depth + 1)
        for table in ("local", "param", "cls", "module"):
            bound = scope[table].get(node.id)
            if bound is not None:
                return _resolve(bound, scope, file, depth + 1)
        imported = _module_consts().get(node.id)
        if imported is not None:
            value, owner = imported
            return _resolve(value, _scope(owner, None), owner, depth + 1)
        return [_UNKNOWN]
    if isinstance(node, ast.Attribute):
        attr = _attr_consts().get(node.attr)
        if attr is not None:
            value, owner = attr
            return _resolve(value, _scope(owner, None), owner, depth + 1)
        return [_UNKNOWN]
    if isinstance(node, ast.Call):
        # `frozenset({...})` / `set({...})` / `tuple([...])` / `list([...])` over
        # a resolvable member list — `frozenset` is the IDIOMATIC shape for a rel
        # family in this package (`DERIVABLE_STRUCTURAL_RELS`, `_ABOUT_REPLAY_RELS`,
        # `ENTITY_LINKED_RELS`), and a `for rel in CONSTANT` loop over one is the
        # same read as a loop over a tuple. Without this branch those loops fell
        # through to the gap and the family constants escaped the census.
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in ("frozenset", "set", "tuple", "list")
            and len(node.args) == 1
        ):
            return _resolve(node.args[0], scope, file, depth + 1)
        # `"|".join(ABOUT_EDGE_TYPES)` — the other call shape that builds a rel
        # pattern: a separator constant joined over a constant's members.
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
            and len(node.args) == 1
        ):
            separators = _resolve(node.func.value, scope, file, depth + 1)
            members = _resolve(node.args[0], scope, file, depth + 1)
            return [sep.join(members) for sep in separators][:_MAX_CANDIDATES]
        return [_UNKNOWN]
    if isinstance(node, ast.IfExp):
        return (
            _resolve(node.body, scope, file, depth + 1)
            + _resolve(node.orelse, scope, file, depth + 1)
        )[:_MAX_CANDIDATES]
    return [_UNKNOWN]


def _may_traverse_every_type(source: str) -> bool:
    """Text pre-filter for the empty-pattern class: is there a relationship
    pattern with no type in it?

    Both spellings count: the bracketed `-[r]->` (no type slot at all) and the
    bracket-less `(n)--()`. Cheap, and only a filter — `_is_empty_pattern_read`
    decides. It must stay a SUPERSET of the class, or a filtered-out file is a
    reader that ships green — which is exactly how the bracket-less member was
    missed while `EMPTY_PATTERN` was floor-checked.
    """
    if any(_type_slot(m.group(1)) is None for m in _EMPTY_TRAVERSAL.finditer(source)):
        return True
    return bool(_BARE_TRAVERSAL.search(source))


def _may_have_gap_type(source: str) -> bool:
    """Text pre-filter for the call-time class: is any type slot an EXPRESSION?

    Also only a filter (`_is_gap_type_read` decides) — but it has to be a
    SUPERSET of the class, or the walk never reaches the site it exists for: a
    filtered-out file is a reader that ships green.
    """
    return bool(_GAP_TYPE_HINT.search(source))


def _docstrings(file: _File) -> set[int]:
    """The `id()`s of every module/class/function docstring Constant.

    A docstring is prose that may QUOTE Cypher (this package's docs do, e.g. a
    "tried `MATCH (n)<-[r]-(m)` and it scans" note), so it is not a site: only an
    executed string literal is. Without this, the class walk would derive a
    function whose docstring merely mentions the pattern.
    """
    found: set[int] = set()
    for node in ast.walk(file.tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            found.add(id(body[0].value))
    return found


def _is_read(text: str) -> bool:
    """Does this Cypher string traverse the edge under a reading clause?

    Per traversal, the *last* governing clause before it decides: `MATCH`/
    `OPTIONAL MATCH` read, `MERGE`/`CREATE` mint.
    """
    for match in _TRAVERSAL.finditer(text):
        clauses = _CLAUSE.findall(text[: match.start()])
        if not clauses:
            continue
        governing = " ".join(clauses[-1].split()).upper()
        if governing in ("MATCH", "OPTIONAL MATCH"):
            return True
    return False


def _type_slot(content: str) -> str | None:
    """What TYPE a bracketed traversal names, decided once for both shapes.

    The type slot is the text after the first `:` in the part BEFORE any property
    map — a colon inside `{...}` is a map key, not a type (which is how
    `MATCH (n)-[r {idx:$i}]->(m)` names no type while carrying a colon).

    * `None` — the bracket carries no type slot at all: `[r]`, `<-[r]-`,
      `[r*1..2]`, `[r {idx:$i}]`. It walks EVERY relationship type.
    * `""` — the slot exists and is EMPTY: `[r:]`, `[:]`, `[r: {c:$c}]`. That is
      what an interpolation the resolver could not fill leaves behind, so this is
      the call-time class's signature.
    * anything else — the type the pattern NAMES (`aboutObject`, `A|B`).
    """
    head = content.split("{", 1)[0]
    if ":" not in head:
        return None
    return re.split(r"[{*\s]", head.split(":", 1)[1].strip(), maxsplit=1)[0]


def _reads_at(text: str, start: int) -> bool:
    """Is the traversal at `start` governed by a reading clause?

    Per traversal, the *last* clause before it decides: `MATCH`/`OPTIONAL MATCH`
    read, `MERGE`/`CREATE` mint. Shared by both shape walks, so neither can drift
    from the other (or from `_is_read`).
    """
    clauses = _CLAUSE.findall(text[:start])
    if not clauses:
        return False
    return " ".join(clauses[-1].split()).upper() in ("MATCH", "OPTIONAL MATCH")


def _is_empty_pattern_read(text: str) -> bool:
    """Does this Cypher traverse a relationship it does NOT name?

    Same governing-clause rule as `_is_read` — a `MATCH`/`OPTIONAL MATCH`
    before the traversal makes it a read — but the subject is the pattern's
    SHAPE: a traversal with no type slot at all (`-[r]->`, `<-[r]-`,
    `-[r {c:$c}]->`, and its bracket-less spelling `(n)--()`) has no type, so
    it walks every relationship type and `aboutObject` is one of them. Both
    spellings are checked, because a reader written in the bracket-less one
    is the same read and was invisible here.

    One exception to the governing-clause rule, ported from the retired
    guard's untyped arm when the two derivations of this class were folded
    into one (#9493): a PATTERN COMPREHENSION enumerates the edges it walks,
    so its bracket is a READ whatever clause introduces it — in
    `SET n.deg = size([(n)-[r]->(m) | r])` the comprehension sits after a
    `SET`, and a comprehension carrying no `MATCH` at all (`RETURN
    size([(n)-[r]-(m) | r]) AS deg`) must still derive, or a walk over every
    relationship type would be discarded as a write — a fail-open in the one
    direction this class exists to close.
    """
    for match in _EMPTY_TRAVERSAL.finditer(text):
        if _type_slot(match.group(1)) is not None:
            continue
        if _reads_at(text, match.start()):
            return True
        if _inside_pattern_comprehension(text, match.start()):
            return True
    return any(_reads_at(text, match.start()) for match in _BARE_TRAVERSAL.finditer(text))


def _is_gap_type_read(text: str) -> bool:
    """Does this Cypher traverse a relationship whose type is a GAP?

    The third shape, and the one no name-based scan can see: the type slot is
    filled by an expression (a parameter, a dict lookup, a journal field), which
    `_resolve` follows one level and then leaves empty — `-[:{rel}]->` resolves to
    `-[:]->`. A bracketed traversal with an empty type slot is therefore the
    signature of a call-time relationship name, and `aboutObject` may be what the
    expression evaluates to at run time.

    A literal `[r:]` would be invalid Cypher, so an empty slot on a resolved
    candidate is always an interpolation the resolver could not fill.
    """
    for match in _EMPTY_TRAVERSAL.finditer(text):
        if _type_slot(match.group(1)) != "":
            continue
        if _reads_at(text, match.start()):
            return True
    return False


def _roots(file: _File) -> list[ast.AST]:
    """The outermost string expressions — the unit a site is derived from.

    One Cypher statement is often several AST nodes: adjacent literals and
    f-string parts concatenate into a `BinOp`/`JoinedStr`, so descending to each
    `Constant` would split a statement and see neither the clause nor the
    relationship name. Only the outermost node is a root; its parts are joined
    by `_resolve`.
    """
    roots: list[ast.AST] = []
    for node in ast.walk(file.tree):
        parent = file.parents.get(node)
        inside_concat = isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Add)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, str):
                continue
            if isinstance(parent, (ast.JoinedStr, ast.FormattedValue)) or inside_concat:
                continue
            roots.append(node)
        elif (
            isinstance(node, ast.JoinedStr)
            or (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add))
        ) and not inside_concat:
            roots.append(node)
    return roots


def _site_symbol(node: ast.AST, file: _File) -> list[str]:
    """The symbol(s) a Cypher expression belongs to.

    The innermost function/method is the site; a module-level constant is
    attributed to the function(s) that consume it, because that is where the
    read happens (`subgraph._CYPHER_ABOUT` is executed by `_collect_raw`).
    """
    chain: list[str] = []
    cur = file.parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chain.append(cur.name)
        cur = file.parents.get(cur)
    if chain:
        # innermost function wins; a `Klass.method` qualifier is a convenience
        # path, not a symbol (the same rule `citation_rot` applies).
        return [chain[0]]

    const_name = None
    for stmt in file.tree.body:
        # `AnnAssign` carries the SAME shape as `Assign` (`NAME: str = "..."`), so a
        # module constant written that way must be attributable to its consumer like
        # any other — without this branch it fell through to the `<module>` fallback
        # and the read lost its symbol. ⚠️ This is NOT what makes the fallback
        # reachable (an annotated constant lands in the branch BELOW); the fallback is
        # reached by a root that sits in no module-level constant statement at all — a
        # `class` body or an `if` block. Both input classes are now exercised on
        # synthetic sources by `test_a_module_constant_is_attributed_to_its_consumer`,
        # so neither branch is a line no arm can reach.
        if isinstance(stmt, ast.AnnAssign):
            if (
                stmt.value is not None
                and isinstance(stmt.target, ast.Name)
                and any(target is node for target in ast.walk(stmt.value))
            ):
                const_name = stmt.target.id
            continue
        if not isinstance(stmt, ast.Assign):
            continue
        if any(target is node for target in ast.walk(stmt.value)):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    const_name = target.id
    if const_name is None:
        return ["<module>"]

    return file.const_users().get(const_name, []) or [f"<module:{const_name}>"]


def _sites_in(file: _File) -> set[tuple[str, int]]:
    """`{(symbol, line)}` — every READ site this file derives.

    NO text gate of its own. `_files()` already admitted this module — on the
    edge's name, a rel-family constant, an every-type pattern, or a gap type —
    and re-testing the first two predicates here re-imposed on the walk exactly
    the blindness `_files()` exists to refute: a module admitted BECAUSE OF ITS
    SHAPE was parsed and then deliberately not walked. The live shape such a gate
    drops is a constant FRAGMENT interpolated into the type slot
    (`_REL = "about"` -> `f"...[:{_REL}Object]..."`), which `_resolve` follows
    and the gate hid — against §13's own promise that a reader *"that names the
    edge — literally, or through a constant the resolver can follow — reds the
    guard"*. It also made the superset arm unfalsifiable: `test_the_file_filter_
    un_sees_nothing` re-uses THIS walk as its proof, so a gate narrowing here was
    invisible to it as well.

    Docstrings are skipped for the reason `_docstrings()` states — prose that
    QUOTES a pattern is not an executed read. The empty-pattern and call-time
    walks both apply that rule; this one did not, so a docstring quoting
    `MATCH (n)-[:aboutObject]->(m)` would have demanded a §13 row for a comment.
    """
    docstrings = _docstrings(file)
    sites: set[tuple[str, int]] = set()
    for node in _roots(file):
        if id(node) in docstrings:
            continue
        candidates = _resolve(node, _scope(file, node), file)
        if not any(EDGE in text and _is_read(text) for text in candidates):
            continue
        line = getattr(node, "lineno", 0)
        for symbol in _site_symbol(node, file):
            sites.add((symbol, line))
    return sites


@lru_cache(maxsize=1)
def _rel_constant_names() -> frozenset[str]:
    """Constants whose own value carries the edge name.

    The census's file filter: a module can build its rel pattern out of
    `SUPERSEDE_STRUCTURAL_RELS` without ever writing the literal, so reading only
    files that contain `aboutObject` would reproduce the blindness one level up.
    """
    names: set[str] = set()
    for table in (_module_consts(), _attr_consts()):
        for name, (value, owner) in table.items():
            if any(EDGE in text for text in _resolve(value, _scope(owner, None), owner)):
                names.add(name)
    return frozenset(names)


@lru_cache(maxsize=1)
def _census() -> dict[tuple[str, str], tuple[int, ...]]:
    """Every site under `tortoise/` whose Cypher traverses the edge.

    `{(relative_path, symbol): (line numbers)}` — READ sites only.

    Cached: three tests read it and it parses the whole package with `ast`.
    """
    sites: dict[tuple[str, str], list[int]] = {}
    for file in _files():
        for symbol, line in sorted(_sites_in(file)):
            sites.setdefault((file.rel, symbol), []).append(line)
    return {key: tuple(value) for key, value in sites.items()}


@lru_cache(maxsize=1)
def _empty_pattern_sites() -> dict[tuple[str, str], tuple[int, ...]]:
    """Every site that reads the edge through a pattern naming NO type.

    A separate walk from `_census()` because the site is not identified by a
    name at all — the class is derived from the SHAPE of the pattern. Kept apart
    so the completeness arm can say which class a missing site belongs to, and
    so the class's own declaration (`EMPTY_PATTERN`) is checked against what the
    code actually does rather than against itself.
    """
    sites: dict[tuple[str, str], list[int]] = {}
    for file in _files():
        if not _may_traverse_every_type(file.source):
            continue
        for symbol, line in sorted(_empty_pattern_sites_in(file)):
            sites.setdefault((file.rel, symbol), []).append(line)
    return {key: tuple(sorted(value)) for key, value in sites.items()}


def _ungated_walk_sites() -> tuple[
    set[tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]]
]:
    """The three derivations run over EVERY module, with no text gate at all.

    ⚠️ **This is the only way the gate can be interrogated, and an earlier version of
    the arm did not do it.** `_files()` is a text predicate in front of the walks, so
    re-filtering the GATED walks' output by module is circular: the excluded set is
    empty by construction, and removing a whole module from `_files()` — measured —
    left every arm in the guard green while a row's reader stopped being derived.
    The question is not "did a gated walk see something in a skipped module" (it
    cannot, by definition) but "does an UNGATED walk find a site the gated one did
    not", and only a walk that ignores the gate can answer it.

    The two shape predicates (`_may_traverse_every_type`, `_may_have_gap_type`) are
    part of the gate, so they are NOT applied here either: a predicate one type
    narrow is the same defect one level down, and it shows up as a site in this
    difference too.

    ⚠️ **The ONLY difference from the gated path is WHICH modules are walked.** The
    per-module derivations are the SAME functions the gated walks call
    (`_sites_in`, `_empty_pattern_sites_in`, `_call_time_sites_in`); this function
    must never grow an inline copy of one. A copy is a blindness channel in the very
    instrument that exists to un-blind: if the copy were later narrowed the gate's
    widening would under-detect instead of reding, and if the gated side changed
    alone the arm would red spuriously — after which its red no longer means "the
    gate is narrow". `test_the_ungated_walk_reuses_the_gated_derivations` pins this
    structurally, by recording which of the helpers ran per module.
    """
    named: set[tuple[str, str]] = set()
    empty: set[tuple[str, str]] = set()
    call_time: set[tuple[str, str]] = set()
    for path, source in _sources().items():
        file = _File(path, str(path.relative_to(ROOT)), source)
        for symbol, _line in _sites_in(file):
            named.add((file.rel, symbol))
        for symbol, _line in _empty_pattern_sites_in(file):
            empty.add((file.rel, symbol))
        for symbol, _line in _call_time_sites_in(file):
            call_time.add((file.rel, symbol))
    return named, empty, call_time


def _narrow_gate_walk_sites() -> tuple[
    set[tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]]
]:
    """The same three derivations over `_literal_files()` — the CONTROL's gate.

    `_literal_files()` is the single predicate the gate started from, so a site it
    misses is a site the gate's LATER predicates rescued. Without a measured
    non-empty difference here the comparison above could not detect a narrowing at
    all, and its silence would mean nothing.
    """
    gate = {file.path for file in _literal_files()}
    named: set[tuple[str, str]] = set()
    empty: set[tuple[str, str]] = set()
    call_time: set[tuple[str, str]] = set()
    for path, source in _sources().items():
        if path not in gate:
            continue
        file = _File(path, str(path.relative_to(ROOT)), source)
        for symbol, _line in _sites_in(file):
            named.add((file.rel, symbol))
        if _may_traverse_every_type(source):
            for symbol, _line in _empty_pattern_sites_in(file):
                empty.add((file.rel, symbol))
        if _may_have_gap_type(source):
            for symbol, _line in _call_time_sites_in(file):
                call_time.add((file.rel, symbol))
    return named, empty, call_time


def _empty_pattern_sites_in(file: _File) -> set[tuple[str, int]]:
    """The empty-pattern derivation for ONE module — the single definition.

    `_empty_pattern_sites()` (gated), `_ungated_walk_sites()` (every module) and
    `_narrow_gate_walk_sites()` (the control) all call this, so no two halves of a
    comparison can drift: the only thing that varies between them is the module set.
    """
    docstrings = _docstrings(file)
    sites: set[tuple[str, int]] = set()
    for node in _roots(file):
        if id(node) in docstrings:
            continue
        candidates = _resolve(node, _scope(file, node), file)
        if not any(_is_empty_pattern_read(text) for text in candidates):
            continue
        line = getattr(node, "lineno", 0)
        for symbol in _site_symbol(node, file):
            sites.add((symbol, line))
    return sites


def _call_time_sites_in(file: _File) -> set[tuple[str, int]]:
    """The call-time derivation for ONE module (usually synthetic).

    The mechanism arm's harness: it lets a synthetic source — a reader of a shape
    the tree does not contain — be run through the SAME derivation the real
    census uses, so "a new member of this class cannot ship green" is measured on
    a source that would be a new member, not on today's tree.
    """
    docstrings = _docstrings(file)
    sites: set[tuple[str, int]] = set()
    for node in _roots(file):
        if id(node) in docstrings:
            continue
        candidates = _resolve(node, _scope(file, node), file)
        if not any(_is_gap_type_read(text) for text in candidates):
            continue
        line = getattr(node, "lineno", 0)
        for symbol in _site_symbol(node, file):
            sites.add((symbol, line))
    return sites


@lru_cache(maxsize=1)
def _call_time_sites() -> dict[tuple[str, str], tuple[int, ...]]:
    """Every site that reads an edge whose relationship TYPE is an expression.

    The third class, and the one the census used to DECLARE instead of derive: a
    relationship name assembled at call time (a parameter, a dict lookup, a
    journal field) leaves the type slot empty when the resolver gives up on it, so
    the shape `-[r:]->` is the class's signature — exactly as `-[r]->` is the
    empty-pattern class's. Deriving it is what makes a NEW reader of the shape red
    instead of shipping green: a hand list can only check the entries it was
    given, which is how `traverse`, `ingest` and the replay reads were missed.

    Kept apart from the other two walks because each class answers a different
    question, and a missing site has to be reported against the class it belongs
    to ("this names no type" and "this names a type nobody can see" are
    different defects with different fixes).
    """
    sites: dict[tuple[str, str], list[int]] = {}
    for file in _files():
        if not _may_have_gap_type(file.source):
            continue
        for symbol, line in sorted(_call_time_sites_in(file)):
            sites.setdefault((file.rel, symbol), []).append(line)
    return {key: tuple(sorted(value)) for key, value in sites.items()}
