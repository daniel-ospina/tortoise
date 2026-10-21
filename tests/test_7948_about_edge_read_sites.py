"""#7948 — the `aboutObject` read-site declaration must be COMPLETE, not just
well-formed.

THE DEFECT THIS PINS
--------------------
`docs/architecture/STORAGE-ARCHITECTURE.md` §13's read-site table is the
**whole evidence base** for a P1-severe storage ruling — the document says so in
its own words: *"⚠️ The count is the whole argument — the table must name every
customer-callable surface"*. The ruling it supports (KEEP the stored edge,
owner, 2026-09-23) is safe; the **table** is not.

It is a hand-maintained prose list of `file.py (symbol)` pinned to
`c79ba1cf2`, and it has been found incomplete **four separate times**:

1. the issue body (`#7948`, 13 sites claimed to be the envelope);
2. review finding `B2.7` (`REVIEW-CONSOLIDATED-2026-09-23.md`) — named three
   further readers and under-classed two of them;
3. the `#7956` erratum on `B2.7`, which found that finding *itself* incomplete
   in the direction that matters (the customer-callable count was four, not
   two);
4. the review of *this* file's first revision (`PR #9398`) — five further
   readers, two of them customer-callable, **all** invisible to a census that
   only read Cypher *string literals*.

THE ROOT CAUSE, AND WHY THIS IS A CENSUS RATHER THAN A LONGER LIST
------------------------------------------------------------------
Nothing executable compared the table to the code. A **symbol resolution** check
cannot see a *missing* row, so the table could only ever be audited by hand — and
hand audits are what produced the four passes above.

The first revision of this guard was itself a hand audit in disguise: it read
`ast.Constant` only, so it could not see a relationship name **built at
runtime** from a repo constant. Five live readers were built that way
(`StateRanker._fetch_object_signals` joins `ranking.ABOUT_EDGE_TYPES`; both
`TortoiseSDK._state_related_*` concatenate `_ABOUT_TYPES`; `supersede_point` and
`_preview_supersede` loop over `SUPERSEDE_STRUCTURAL_RELS`) and every arm stayed
green over them — the census and the table agreed perfectly *because* the
census could only see what a single literal names. That agreement was circular.

So the census resolves the *expression*, not just the leaf:

* `Constant` strings — the plain case;
* `+`-concatenation and **f-strings** (`JoinedStr`, even with no placeholders),
  joined into one string;
* `Name`/`Attribute` references to a **string constant or tuple of strings**,
  module-level or class-level, in this file or (when the name is unique
  repo-wide) in the module that defines it — so an `import`ed constant resolves;
* `NAME = "a|b|…"` joined via `"|".join(NAME)`, and `for rel in NAME` loop
  variables, expanded to the constant's members.

WHAT THIS PINS
--------------
1. **`test_every_code_site_that_traverses_the_edge_is_declared`** — COMPLETENESS.
   The census is derived from the source's AST, not from a list: every Cypher
   string that traverses the `aboutObject` relationship *by name* — written
   literally or built from a constant — is a site, and every site must appear in
   §13's table. A new reader added anywhere under `tortoise/` reds this until it
   is declared.
2. **`test_every_declared_symbol_still_performs_the_read`** — ROT. Every symbol
   the table names must be a census site, or the one documented un-derivable
   site (which must still exist in its file). A row whose symbol resolves but
   does not read misleads every re-verifier — `EXTRACTOR-V4-ARCHITECTURE`'s own
   rule is *"the symbol name is authoritative, the line number is a
   convenience"*.
3. **`test_the_declaration_covers_every_known_un_derivable_site`** — the reader
   no census can derive is declared **with its reason** and still exists.
   `recall_subgraph` reads the edge through `MATCH (n)-[r]->(m)` (its default
   `completeness="full"` sets an empty relationship pattern), so no relationship
   name appears anywhere in the statement.
4. **`test_the_table_is_not_vacuous`** — the census, the table and the
   customer-callable count are all non-trivially populated, so none of the arms
   above can pass by finding nothing.
5. **`test_the_census_derives_a_relationship_name_built_at_runtime`** — the
   MECHANISM, on synthetic sources: a literal, a `+`-concatenation, an f-string,
   `"|".join(TUPLE)` and a `for rel in TUPLE` loop must each derive; a `MERGE`
   writer must not. Without this arm the resolver could regress to
   literal-only and the table would silently go back to being un-checked in the
   class that produced the fourth correction.
6. **`test_the_prose_counts_match_the_table`** — the NUMBERS. §13 states the
   census size and the customer-callable count in prose and the other arms check
   only the rows, so the numerals are read out of the sentences that state them
   and compared to the table. A demoted `reach` cell with a stale headline is
   the `#7956` erratum's direction — the one the ruling rests on.

WHAT THE CENSUS CANNOT SEE (stated, not silently excluded)
---------------------------------------------------------
* **An empty relationship pattern** (`MATCH (n)-[r]->(m)`) names no type, so
  nothing literal or constant carries `aboutObject` — hence arm 3.
* **Resolution is one level deep per binding.** A rel name assembled from a
  value that is itself computed at call time (a function argument, a dict
  lookup, a value stored on an instance at construction) is not resolved; the
  placeholder it leaves behind is a *gap the literal around it still fills*, so
  a query whose rel pattern is readable still derives. Anything assembled
  entirely from such values must be declared by name (arm 3) or a row will be
  missing.
* **Write arms are out of scope.** A `MERGE`/`CREATE` that mints the edge is a
  writer, separated from a reader by the governing Cypher clause — not by a
  keyword appearing anywhere in the statement. A read on the *write path*
  (e.g. `supersede_point` moving the edge) IS a row: it reads before it writes.

NOT promised, and deliberately so:
* Line numbers are NOT asserted. The documents' own rule makes the symbol the
  authority and the line a convenience, and pinning line numbers would make
  this guard red on every unrelated edit — a different way to be dead.
* Whether a declared site is *reachable* (default-ON, flag-gated, eval-only) is
  NOT asserted: that column is prose with reasons, and a regex over the whole
  of it would pin wording rather than behaviour.

The customer-callable **count** IS asserted (arm 6), because §13 calls it *"the
whole argument"*: the arm reads the numeral out of the sentence that states it
and compares it to the `reach` column, so demoting two rows without moving the
number — the `#7956` erratum's exact direction, and the error the KEEP ruling
rests on — reds instead of shipping. The same arm checks the census-size
numeral (`TWENTY read sites`) against `census ∪ UN_DERIVABLE`. Only the numeral
is read, never the prose around it, so the sentence can be rewritten freely as
long as it stays true.

Static, stdlib-only, no network / DB / graph — it reads source text and a
document.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TORTISE = ROOT / "tortoise"
STORAGE = ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"

#: The edge the §13 ruling is about.
EDGE = "aboutObject"

#: The Cypher clauses that can govern a relationship pattern. `MERGE`/`CREATE`
#: mint the edge, so they are writers; `MATCH`/`OPTIONAL MATCH` are the readers.
_CLAUSE = re.compile(r"\b(OPTIONAL\s+MATCH|MATCH|MERGE|CREATE)\b")
#: A traversal: an opening `[` or `-` shortly before the relationship name, so
#: prose ("an ``aboutObject`` edge") is not mistaken for Cypher.
_TRAVERSAL = re.compile(rf"[-\[][^\n]{{0,140}}?{EDGE}", re.IGNORECASE)
#: The most candidate strings one expression may expand to. A rel list is at most
#: a handful of members, so this only bounds pathological input.
_MAX_CANDIDATES = 64
#: What an unresolvable interpolation contributes: a GAP, so the literal around
#: it still derives. Deliberately empty rather than a marker string — the point
#: is to keep the surrounding Cypher intact, not to guess the value.
_UNKNOWN = ""
#: A backticked token in §13's `site` column that can be a symbol.
_SYMBOL = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?\Z")

#: The floor arms below: deliberately BELOW today's census (19 sites / 16 rows),
#: because a row deleted together with its reader is not a reason to red — that
#: is a legitimate contraction, and a floor pinned at today's exact count turns
#: it into a failure whose message says "the parser broke". The floor exists only
#: so no arm can pass by finding nothing.
_MIN_SITES = 10
_MIN_ROWS = 10
#: `FOUR` was the old floor; the table now declares six customer-callable
#: surfaces. Kept at the historical count so a *contraction* of the prose is
#: visible even before the exact-count arm below reads the numeral itself.
_MIN_CUSTOMER_CALLABLE = 4

#: The sites NO census can derive, each with the reason it cannot be derived.
UN_DERIVABLE: tuple[tuple[str, str, str], ...] = (
    (
        "ranking.py",
        "_neighbors",
        "`completeness=\"full\"` sets an empty relationship pattern, so the "
        "query walks EVERY relationship type via `MATCH (n)-[r]->(m)` and "
        "reads the edge without ever naming it",
    ),
)

#: The table header that identifies §13's declaration.
_TABLE_HEADER = "| # | site |"

#: The §13 bullet whose numerals the ruling rests on. Everything between this
#: line and the next top-level bullet is the `aboutObject` argument; the section
#: also carries numerals about other rulings (a `123`-hub cap), so the count arm
#: must not read past the bullet.
_BULLET_START = "- **`aboutObject` as a stored edge vs a derived join**"
#: `<N> read sites` — the census size, asserted.
_PROSE_SITES = re.compile(r"\b([A-Za-z]+)\s+read sites\b", re.IGNORECASE)
#: `<N> of them are CUSTOMER-CALLABLE` — the headline count, asserted.
_PROSE_CALLABLE = re.compile(
    r"\b([A-Za-z]+)\s+of them are customer-callable\b", re.IGNORECASE
)
#: `... are <N> of them` — a later sentence re-asserting the same count. Any
#: sentence that restates the number is a claim, so it is checked too: the
#: `#7956` erratum was exactly a second sentence contradicting the first.
_PROSE_CALLABLE_AGAIN = re.compile(r"\bare\s+([A-Za-z]+)\s+of them\b", re.IGNORECASE)

#: The number words §13 states its counts in. Digits are accepted as well, so
#: a rewrite that writes `6` instead of `SIX` stays checkable.
_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twenty-one": 21,
    "twenty-two": 22,
    "twenty-three": 23,
    "twenty-four": 24,
    "twenty-five": 25,
    "thirty": 30,
}


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
    census from reproducing its old blindness one level up.
    """
    parsed = {file.path: file for file in _literal_files()}
    names = _rel_constant_names()
    for path, source in _sources().items():
        if path in parsed:
            continue
        if any(name in source for name in names):
            parsed[path] = _File(path, str(path.relative_to(ROOT)), source)
    return tuple(sorted(parsed.values(), key=lambda file: file.path))


@lru_cache(maxsize=1)
def _module_consts() -> dict[str, tuple[ast.AST, _File]]:
    """Module-level constants defined **exactly once** in the package.

    The census falls back to this index for a name it cannot bind in its own
    module — an `import`ed constant (`mcp_server` imports
    `SUPERSEDE_STRUCTURAL_RELS` from `sdk`). The uniqueness rule is what keeps
    the fallback from guessing between two different constants that happen to
    share a name: an ambiguous name resolves to nothing and is reported as a gap
    by the arm that covers un-derivable sites.
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


def _scope(file: _File, node: ast.AST | None) -> dict[str, dict[str, ast.AST]]:
    """The bindings visible at `node`: loop vars, locals, class attrs, globals.

    Innermost-first on every table: a loop variable shadows a local of the same
    name, which shadows the class attribute, which shadows the module global.
    """
    scope: dict[str, dict[str, ast.AST]] = {
        "loop": {},
        "local": {},
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
    if isinstance(node, (ast.Tuple, ast.List)):
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
        for table in ("local", "cls", "module"):
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
        # `"|".join(ABOUT_EDGE_TYPES)` — the one call shape that builds a rel
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
    """`{(symbol, line)}` — every READ site this file derives."""
    if EDGE not in file.source and not any(
        name in file.source for name in _rel_constant_names()
    ):
        return set()
    sites: set[tuple[str, int]] = set()
    for node in _roots(file):
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
def _declared_rows() -> tuple[tuple[int, str, str, tuple[tuple[str, str], ...]], ...]:
    """§13's table, parsed: `(row_number, site_cell, reach_cell, site_pairs)`.

    The document is the declaration — there is no second manifest to drift.
    """
    lines = STORAGE.read_text(encoding="utf-8").splitlines()
    rows: list[tuple[int, str, str, tuple[tuple[str, str], ...]]] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if not in_table:
            if stripped.startswith(_TABLE_HEADER):
                in_table = True
            continue
        if not stripped.startswith("|"):
            break
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        number, site_cell, reach_cell = int(cells[0]), cells[1], cells[3]
        rows.append((number, site_cell, reach_cell, _row_site_pairs(site_cell)))
    return tuple(rows)


def _row_site_pairs(site_cell: str) -> tuple[tuple[str, str], ...]:
    """The `(basename, base_symbol)` pairs one `site` cell actually claims.

    Built **positionally**: symbols following a file token belong to that file,
    so a row citing two files with two differently-owned symbols cannot satisfy
    four pairings the row never meant (the cross-product loosening). The table
    is prose, so this is the one mechanical pairing available — and it is
    exact, which is what an arm whose whole purpose is "an omitted reader
    cannot pass" needs.

    Line numbers are never asserted (see the docstring), so a row may carry one
    or omit it. Symbols are the backticked tokens that are not a filename and
    not an issue reference.
    """
    pairs: list[tuple[str, str]] = []
    current: str | None = None
    for raw in re.findall(r"`([^`]+)`", site_cell):
        token = raw.strip()
        file_match = re.fullmatch(r"([A-Za-z0-9_/]+\.py)(?::\d+)?", token)
        if file_match:
            current = file_match.group(1).rsplit("/", 1)[-1]
            continue
        if token.startswith("#") or ":" in token or not _SYMBOL.match(token):
            continue
        if current is None:
            # A symbol with no file before it names no site.
            continue
        pairs.append((current, _last(token)))
    return tuple(pairs)


def _last(symbol: str) -> str:
    """The base symbol: `ranking.SubgraphExpander._neighbors` -> `_neighbors`."""
    return re.sub(r"\(\)$", "", symbol).rsplit(".", 1)[-1]


def _numeral(token: str) -> int | None:
    """`SIX` -> 6, `20` -> 20, a word this table does not know -> `None`.

    Returning `None` rather than dropping the token is deliberate: a count the
    arm cannot read must be reported, because silently skipping it is how an
    unreadable number becomes an unguarded one.
    """
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


@lru_cache(maxsize=1)
def _prose_counts() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """§13's asserted counts, verbatim: `(read sites, customer-callable)`.

    The tokens are returned as written, not as numbers, so the arm can name the
    sentence's own word when it disagrees with the table.
    """
    lines = STORAGE.read_text(encoding="utf-8").splitlines()
    bullet: list[str] = []
    inside = False
    for line in lines:
        if not inside:
            inside = line.startswith(_BULLET_START)
            continue
        if line.startswith("- **") or line.startswith("---"):
            break
        bullet.append(line)
    text = "\n".join(bullet)
    callable_tokens = [m.group(1) for m in _PROSE_CALLABLE.finditer(text)]
    callable_tokens += [m.group(1) for m in _PROSE_CALLABLE_AGAIN.finditer(text)]
    return tuple(m.group(1) for m in _PROSE_SITES.finditer(text)), tuple(callable_tokens)


def test_the_table_is_not_vacuous():
    """Every arm below must be able to find something, or all of them pass by
    finding nothing — the failure mode that let four hand audits conclude the
    list was complete."""
    sites = _census()
    rows = _declared_rows()
    customer_callable = [row for row in rows if "customer-callable" in row[2].lower()]

    assert len(sites) >= _MIN_SITES, (
        f"the census derived only {len(sites)} read sites from tortoise/ — the "
        "traversal shape changed (or the parser broke) and the completeness arm "
        "would pass vacuously. Sites: "
        + ", ".join(sorted(f"{f}::{s}" for f, s in sites))
    )
    assert len(rows) >= _MIN_ROWS, (
        f"§13's table parsed to {len(rows)} rows — the table moved out of the "
        "shape this guard reads, so nothing below is being checked."
    )
    assert len(customer_callable) >= _MIN_CUSTOMER_CALLABLE, (
        "§13 must name at least the customer-callable surfaces the KEEP ruling "
        "rests on; the table declares "
        f"{len(customer_callable)}."
    )
    # The un-derivable declaration is the census's only hand-maintained surface,
    # so it must not quietly empty out.
    assert UN_DERIVABLE, (
        "the un-derivable declaration is empty — either every site is derivable "
        "(delete this arm and the test below) or the hand-declared blind spot "
        "was dropped without a replacement."
    )


def test_the_prose_counts_match_the_table():
    """The NUMBERS are the argument, so they are asserted, not narrated.

    §13 states its own totals in prose — *"TWENTY read sites, and SIX of them
    are CUSTOMER-CALLABLE"* — and the KEEP ruling rests on the customer-callable
    one. The arms above guard the **rows**; nothing guarded the **numerals**, so
    a row demoted in the `reach` column left every test green while the headline
    still said SIX. That is the `#7956` erratum's direction (the count was
    under-stated), and it is the error this table exists to prevent.

    Only the numeral is read — never the prose around it — so the sentence stays
    free to be rewritten as long as it stays true.
    """
    site_tokens, callable_tokens = _prose_counts()
    customer_callable = [
        row for row in _declared_rows() if "customer-callable" in row[2].lower()
    ]
    # The census, plus the site no census can derive: `_neighbors` reads the
    # edge through an empty pattern, so it is a read site the table must name
    # even though no derivation finds it.
    derived = _census()
    # Same `(basename, symbol)` shape the table is compared in — the census
    # keys are repo-relative paths, a row names a file.
    read_sites = {(path.rsplit("/", 1)[-1], symbol) for path, symbol in derived} | {
        (filename, _last(symbol)) for filename, symbol, _reason in UN_DERIVABLE
    }

    problems: list[str] = []
    if not site_tokens:
        problems.append(
            "§13 no longer states `<N> read sites`, so the number this arm "
            "polices is unguarded again (restore the sentence, or delete this "
            "arm and say why in the docstring)"
        )
    for token in site_tokens:
        stated = _numeral(token)
        if stated is None:
            problems.append(
                f"§13 states `{token}` read sites and this guard cannot read "
                "that numeral — spell it out in words or digits"
            )
        elif stated != len(read_sites):
            problems.append(
                f"§13 states `{token}` read sites; the census derives "
                f"{len(derived)} and {len(read_sites) - len(derived)} more can "
                f"only be declared by name, so there are {len(read_sites)} read "
                "sites to declare"
            )

    if not callable_tokens:
        problems.append(
            "§13 no longer states how many of the sites are customer-callable — "
            "that is the number the KEEP ruling rests on, so it cannot go "
            "unstated"
        )
    for token in callable_tokens:
        stated = _numeral(token)
        if stated is None:
            problems.append(
                f"§13 states `{token}` customer-callable sites and this guard "
                "cannot read that numeral — spell it out in words or digits"
            )
        elif stated != len(customer_callable):
            problems.append(
                f"§13 states `{token}` customer-callable sites; the `reach` "
                f"column declares {len(customer_callable)}: "
                + ", ".join(f"row {row[0]}" for row in customer_callable)
            )

    assert not problems, (
        "the counts §13 asserts do not match the table it asserts them about. "
        "⚠️ The customer-callable number IS the argument for keeping the edge — "
        "a stale numeral here is the ruling resting on an unmeasured claim:\n  - "
        + "\n  - ".join(problems)
    )


def test_every_code_site_that_traverses_the_edge_is_declared():
    """COMPLETENESS: the table must name every reader the code actually has.

    This is the arm that no symbol-resolution check can replace — a *missing*
    row has no symbol to fail to resolve.
    """
    sites = _census()
    declared = {
        pair for _n, _cell, _reach, pairs in _declared_rows() for pair in pairs
    }

    undeclared = []
    for (path, symbol), lines in sorted(sites.items()):
        if (path.rsplit("/", 1)[-1], symbol) in declared:
            continue
        undeclared.append((path, symbol, lines))

    assert not undeclared, (
        "these sites traverse the `aboutObject` edge and §13's table does not "
        "name them. That table is the evidence base for the KEEP ruling, and "
        "the document's own rule is that the count IS the argument — so an "
        "omitted reader is an unmeasured one (add the row, with the symbol, to "
        "`## 13` of STORAGE-ARCHITECTURE.md):\n  - "
        + "\n  - ".join(
            f"{path}::{symbol} (lines {lines})" for path, symbol, lines in undeclared
        )
    )


def test_every_declared_symbol_still_performs_the_read():
    """ROT: a row whose symbol resolves but does not read is a false row.

    All four prior audits failed in this direction too — a symbol that exists,
    is quoted, and is not where the traversal happens — so the check is that the
    symbol is a **census site**, not merely present in the file.
    """
    sites = _census()
    site_symbols: set[tuple[str, str]] = {
        (path.rsplit("/", 1)[-1], symbol) for path, symbol in sites
    }
    un_derivable = {(filename, _last(symbol)) for filename, symbol, _reason in UN_DERIVABLE}

    problems: list[str] = []
    for number, _site_cell, _reach, pairs in _declared_rows():
        for key in pairs:
            if key in site_symbols:
                continue
            if key in un_derivable:
                # Declared-by-design, but the symbol must still exist —
                # otherwise the row rotted silently and `UN_DERIVABLE` is
                # masking it.
                filename, symbol = key
                candidates = [p for p in TORTISE.rglob(filename)]
                if not candidates:
                    problems.append(f"row {number}: uncovered site {filename} does not exist")
                elif not any(
                    symbol in p.read_text(encoding="utf-8") for p in candidates
                ):
                    problems.append(
                        f"row {number}: un-derivable symbol `{symbol}` is not in {filename}"
                    )
                continue
            problems.append(
                f"row {number}: `{key[1]}` ({key[0]}) is declared as a reader but no "
                "Cypher in it traverses `aboutObject`"
            )

    assert not problems, (
        "§13's table names these as readers of the `aboutObject` edge, and they "
        "are not — a re-verifier following the row finds no read and concludes "
        "the passage is wrong (the exact failure EXTRACTOR-V4-ARCHITECTURE.md's "
        "citation guard exists for, one document over):\n  - "
        + "\n  - ".join(problems)
    )


def test_the_declaration_covers_every_known_un_derivable_site():
    """The reader no census can find must be declared BY NAME, with the reason,
    and must still exist — a scan for a relationship name is structurally blind
    to an empty pattern, so a dropped row has no other way to be noticed."""
    declared = {pair for _n, _cell, _reach, pairs in _declared_rows() for pair in pairs}

    missing = []
    for filename, symbol, reason in UN_DERIVABLE:
        assert _last(symbol) in "".join(
            path.read_text(encoding="utf-8") for path in TORTISE.rglob(filename)
        ), f"`{symbol}` no longer exists in {filename} — re-verify the row"
        if (filename, _last(symbol)) not in declared:
            missing.append(f"{filename}::{symbol} — {reason}")

    assert not missing, (
        "these sites read the `aboutObject` edge but cannot be derived by any "
        "scan for the relationship name, so §13 must name them explicitly (it "
        "is the only place they are recorded):\n  - " + "\n  - ".join(missing)
    )


#: Synthetic sources for the mechanism arm below: the shapes a relationship name
#: reaches a query in. The fifth is a WRITER and must derive as nothing.
_MECHANISM_CASES: tuple[tuple[str, str, bool], ...] = (
    (
        "literal",
        'def read(g):\n    g.query("MATCH (n)-[:aboutObject]->(m) RETURN n")\n',
        True,
    ),
    (
        "plus-concatenation of a constant",
        'REL = "aboutObject"\n\n\ndef read(g):\n'
        '    g.query("MATCH (n)-[:" + REL + "]->(m) RETURN n")\n',
        True,
    ),
    (
        "f-string of a constant",
        'REL = "aboutObject"\n\n\ndef read(g):\n'
        '    g.query(f"MATCH (n)-[:{REL}]->(m) RETURN n")\n',
        True,
    ),
    (
        '`"|".join(TUPLE)`',
        'RELS = ("x", "aboutObject")\n\n\ndef read(g):\n'
        '    pattern = "|".join(RELS)\n'
        '    g.query("MATCH (n)-[r:" + pattern + "]->(m) RETURN n")\n',
        True,
    ),
    (
        "loop over a constant tuple",
        'RELS = ("x", "aboutObject")\n\n\ndef read(g):\n'
        "    for rel in RELS:\n"
        '        g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
        True,
    ),
    (
        "MERGE writer",
        'def write(g):\n    g.query("MERGE (n)-[:aboutObject]->(m) RETURN n")\n',
        False,
    ),
)


def test_the_census_derives_a_relationship_name_built_at_runtime():
    """The MECHANISM, isolated: every shape a rel name reaches a query in.

    The first revision of this guard read `ast.Constant` only, so five live
    readers that build the name at runtime were invisible to it while all four
    arms stayed green. These cases pin the derivation itself, on sources that do
    not depend on today's tree — a literal, a `+`-concatenation, an f-string,
    `"|".join(TUPLE)` and a `for rel in TUPLE` loop.
    """
    missing = []
    for label, source, should_derive in _MECHANISM_CASES:
        derived = _sites_in(_File(TORTISE / "<synthetic>", "<synthetic>", source))
        if bool(derived) is not should_derive:
            missing.append(
                f"{label}: expected {'a site' if should_derive else 'no site'}, "
                f"derived {sorted(derived)}"
            )
    assert not missing, (
        "the census lost a derivation shape, so a reader written that way would "
        "pass this guard undeclared:\n  - " + "\n  - ".join(missing)
    )
