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
   the table names must be a site of one of the two derivations below. A row
   whose symbol resolves but does not read misleads every re-verifier —
   `EXTRACTOR-V4-ARCHITECTURE`'s own rule is *"the symbol name is authoritative,
   the line number is a convenience"*.
3. **`test_the_empty_pattern_class_is_declared`** — the SECOND derivation.
   An empty relationship pattern (`MATCH (n)-[r]->(m)`) names no type, so it
   reads the edge without naming it and no scan for the name can find it —
   `recall_subgraph`'s `completeness="full"`, `entityProfile`/`tortoise_traverse`'s
   per-hop query, and the backfill census's unlabelled inbound count are all
   this shape. It is derived as a **class** from the pattern's own shape: every
   such reader under `tortoise/` must be either a §13 row or a named member of
   `EMPTY_PATTERN` (with the reason it is not an `aboutObject` read for a
   referent), and every member must still derive. One example declared by name
   was the shape's defect: it let three further members — two of them
   customer-callable — go absent while every arm stayed green.
4. **`test_the_table_is_not_vacuous`** — the census, the empty-pattern class,
   the table and the customer-callable count are all non-trivially populated, so
   none of the arms above can pass by finding nothing.
5. **`test_the_census_derives_a_relationship_name_built_at_runtime`** — the
   MECHANISM, on synthetic sources: a literal, a `+`-concatenation, an f-string,
   `"|".join(TUPLE)` and a `for rel in TUPLE` loop must each derive; a `MERGE`
   writer must not. Without this arm the resolver could regress to
   literal-only and the table would silently go back to being un-checked in the
   class that produced the fourth correction.
6. **`test_the_undeclarable_shape_is_declared_by_name`** — the shape NEITHER
   census can derive: a relationship name assembled entirely from a call-time
   value (a dict lookup, an instance attribute). Nothing derives it, so §13 must
   name it — and the declaration is checked in both directions, because the
   revision before this one SAID the class was "declared by name instead" while
   declaring nothing at all, with two about*-specific readers of the shape live
   (`link_entity`, `bind_point_subjects`). A parameter default is resolvable and
   is therefore derived (row 19); the dict-lookup member is row 20 and is also
   registered here.
7. **`test_the_prose_counts_match_the_table`** — the NUMBERS. §13 states its
   counts in prose and the other arms check only the rows, so the numerals are
   read out of the sentences that state them and compared to the table. A
   demoted `reach` cell with a stale headline is the `#7956` erratum's direction
   — the one the ruling rests on. ⚠️ **The counts are checked WITH their units**:
   the headline used to read *"TWENTY read sites, and SIX of them are
   CUSTOMER-CALLABLE"*, mixing a **symbol** count with a **row** count, so "of
   them" could not hold — the six customer-callable rows carried nine symbols.
   It now states sites, rows, customer-callable rows and the sites inside them
   separately, and each numeral is checked against its own unit.
8. **`test_the_census_prose_carries_no_line_citations`** — the LINE HINTS. §13
   states its own rule (*the symbols are authoritative and the line numbers are
   deliberately omitted*) and then, one document over, the sibling paragraph went
   on carrying fifteen `file.py:NNNN` hints — six of them pointing at unrelated
   code at the head that rewrote it. A line number is not a claim this guard can
   check (asserting it would red on every unrelated edit), so the claim is
   removed instead of trusted: the census prose carries none.
9. **`test_the_prose_names_every_row_in_the_empty_pattern_class`** — the CLASS
   SENTENCE. §13 declares the empty-pattern class *"by rows"*, and that list is
   a prose restatement of arm 3's derived set: it said *"rows 3 and 17"* while
   the class had three members (row 18 is the backfill census). An
   under-counted declaration is the same defect as an under-counted table, one
   level up, so the sentence is compared to the derived set.
10. **`test_the_reach_column_names_only_symbols_that_exist`** — the `reach`
   COLUMN'S SYMBOLS. Every backticked identifier in `reach` must exist in the
   package: row 7 cited `QueryOptions`, which is defined nowhere. The arms above
   resolved the `site` column only, so an unguarded column could name a symbol
   that never existed and stay green.

WHAT THE CENSUS CANNOT SEE (stated, not silently excluded)
---------------------------------------------------------
* **An empty relationship pattern** names no type, so nothing literal or
  constant carries `aboutObject` — no scan for the NAME can find it. It is not
  left undeclared: the class is derived by arm 3's shape walk, and every member
  must be a row or an `EMPTY_PATTERN` entry. A pattern assembled **entirely from
  a variable** (no bracket text anywhere in the source) is still out of reach of
  that walk, so it must be declared by name — arm 6 checks that declaration —
  while a parameter DEFAULT (the shape `link_entity` uses) is resolvable and is
  derived instead.
* **Resolution is one level deep per binding.** A rel name assembled from a
  value that is itself computed at call time (a function argument, a dict
  lookup, a value stored on an instance at construction) is not resolved; the
  placeholder it leaves behind is a *gap the literal around it still fills*, so
  a query whose rel pattern is readable still derives. Anything assembled
  entirely from such values must be declared by name (arm 6) or a row will be
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
numerals (sites, rows and the sites inside the customer-callable rows) against
their own units, so the numerals are never restated here a second time: a copy in
this docstring is the same circular agreement the table's sibling copy was. Only
the numeral is read, never the prose around it, so the sentence can be rewritten
freely as long as it stays true.

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
EXTRACTOR = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"

#: The edge the §13 ruling is about.
EDGE = "aboutObject"

#: The Cypher clauses that can govern a relationship pattern. `MERGE`/`CREATE`
#: mint the edge, so they are writers; `MATCH`/`OPTIONAL MATCH` are the readers.
_CLAUSE = re.compile(r"\b(OPTIONAL\s+MATCH|MATCH|MERGE|CREATE)\b")
#: A traversal: an opening `[` or `-` shortly before the relationship name, so
#: prose ("an ``aboutObject`` edge") is not mistaken for Cypher.
_TRAVERSAL = re.compile(rf"[-\[][^\n]{{0,140}}?{EDGE}", re.IGNORECASE)
#: An EMPTY relationship pattern: a bracketed traversal that names NO type
#: (`-[r]->`, `<-[r]-`, `-[r*1..2]->`). It walks EVERY relationship type, so it
#: reads the edge without naming it — the class no scan for the NAME can find.
_EMPTY_TRAVERSAL = re.compile(r"[-<]\s*\[([^\]]*)\]\s*[-<>]")
#: The most candidate strings one expression may expand to. A rel list is at most
#: a handful of members, so this only bounds pathological input.
_MAX_CANDIDATES = 64
#: What an unresolvable interpolation contributes: a GAP, so the literal around
#: it still derives. Deliberately empty rather than a marker string — the point
#: is to keep the surrounding Cypher intact, not to guess the value.
_UNKNOWN = ""
#: A backticked token in §13's `site` column that can be a symbol.
_SYMBOL = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?\Z")

#: Floor for the empty-pattern class walk, so the class arm cannot pass by
#: deriving nothing (today's census: 23 members).
_MIN_EMPTY_PATTERN = 5

#: Floor arms: deliberately BELOW today's census (19 named sites / 16 rows at the
#: time of writing), because a row deleted together with its reader is not a
#: reason to red — that is a legitimate contraction, and a floor pinned at
#: today's exact count turns it into a failure whose message says "the parser
#: broke". The floor exists only so no arm can pass by finding nothing.
_MIN_SITES = 10
_MIN_ROWS = 10
#: `FOUR` was the floor when this arm was written, i.e. the count BEFORE the
#: fifth correction (four → six customer-callable rows). Kept at that historical
#: value so a *contraction* of the prose is visible even before the exact-count
#: arm below reads the numeral itself; the table declares seven today.
_MIN_CUSTOMER_CALLABLE = 4

#: The empty-pattern readers that are NOT an `aboutObject` read for a referent,
#: declared by name with the reason, so a NEW member of the class cannot appear
#: silently: `test_the_empty_pattern_class_is_declared` derives the class from
#: the pattern's shape and requires every member to be either a §13 row or an
#: entry here. Each entry is `(filename, symbol, why it is not an about read)`.
EMPTY_PATTERN: tuple[tuple[str, str, str], ...] = (
    (
        "tortoise/hosted_api.py",
        "_export_graph_snapshot",
        "a whole-graph export — it copies every node and edge, so it carries the "
        "edge as data, not as a read for a referent",
    ),
    (
        "tortoise/hosted_api.py",
        "_load_commit_graph_state",
        "restore/rollback bookkeeping — it rewires every edge type it finds, "
        "`aboutObject` included, but asks nothing about a referent",
    ),
    (
        "tortoise/hosted_backup.py",
        "_independent_counts",
        "an edge-count cross-check (`MATCH ()-[r]->() RETURN count(r)`) — it "
        "counts the edge, it does not read it for a referent",
    ),
    (
        "tortoise/hosted_backup.py",
        "_restore_copy_settled",
        "the post-restore settle check, again a total-edge count",
    ),
    (
        "tortoise/hosted_backup.py",
        "dump_graph",
        "the dump cursor walks every edge type to serialise them",
    ),
    (
        "tortoise/hosted_backup.py",
        "restore_graph",
        "the restore replay that writes those edges back",
    ),
    (
        "tortoise/mcp_server.py",
        "_preview_delete_edges",
        "the delete preview enumerates every edge touching the node — a "
        "deletion inventory, not an about* read",
    ),
    (
        "tortoise/mining.py",
        "verify_no_auto_wire",
        "the no-auto-wire verifier walks every edge type to assert a shape is "
        "absent",
    ),
    (
        "tortoise/projection/__init__.py",
        "_rebuild_all_replay",
        "the rebuild/rollback replay — every edge is deleted and rewritten by "
        "type, so it carries the edge rather than reading it",
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
        "_promote_incident_operators",
        "the operator-promotion pass repoints every edge of an incident node",
    ),
    (
        "tortoise/sdk.py",
        "backfill_v25",
        "a migration — it counts all edges as a baseline",
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
    (
        "tortoise/subgraph.py",
        "_hub_degrees",
        "hub-degree counting for the fan-out cap — it counts every edge of a "
        "node, whatever its type",
    ),
)

#: The readers of the SECOND un-derivable shape: a relationship name assembled
#: ENTIRELY from a call-time value (a dict lookup, an instance attribute, a value
#: passed in from a caller). The resolver follows a name one level deep and then
#: leaves a gap, so such a reader derives nothing from either census — it is the
#: one shape that can only be DECLARED. Each entry is
#: `(filename, symbol, the value it interpolates)`.
#:
#: ⚠️ This is not a second hand list standing in for a scan: the arm below
#: asserts every entry is a §13 ROW *and* that neither census derives it. If the
#: resolver ever learns to follow it the entry goes stale and reds; if a row is
#: dropped the entry reds; and a new reader of the shape has nowhere to hide
#: except here, which is a reviewed declaration rather than silence. (The first
#: member of this class that IS resolvable — a parameter default — is derived
#: instead: `session_link.link_entity`.)
UNDERIVABLE_CALL_TIME: tuple[tuple[str, str, str], ...] = (
    (
        "tortoise/subject_binding.py",
        "bind_point_subjects",
        '`edge_type`, from `_ROLE_TARGET["object"][1]` — a dict lookup at call '
        "time, so the re-probe `MATCH (s:Point)-[r:{edge_type}]->(t)` names no "
        "relationship and no constant resolves it",
    ),
)

#: The table header that identifies §13's declaration.
_TABLE_HEADER = "| # | site |"

#: The §13 bullet whose numerals the ruling rests on. Everything between this
#: line and the next top-level bullet is the `aboutObject` argument; the section
#: also carries numerals about other rulings (a `123`-hub cap), so the count arm
#: must not read past the bullet.
_BULLET_START = "- **`aboutObject` as a stored edge vs a derived join**"
#: `<N> read sites` — the SITE count, asserted.
_PROSE_SITES = re.compile(r"\b([A-Za-z][A-Za-z-]*)\s+read sites\b", re.IGNORECASE)
#: `<N> rows` — the ROW count, asserted. The two units are different (a row may
#: name several sites), which is why each numeral is read with its own noun.
_PROSE_ROWS = re.compile(r"\bacross\s+([A-Za-z][A-Za-z-]*)\s+rows\b", re.IGNORECASE)
#: `<N> of those rows (<M> sites) are CUSTOMER-CALLABLE` — rows and the sites
#: inside them, asserted separately.
_PROSE_CALLABLE_ROWS = re.compile(
    r"\b([A-Za-z][A-Za-z-]*)\s+of those rows\s*\(\s*([A-Za-z][A-Za-z-]*)\s+sites\s*\)"
    r"\s+are\s+CUSTOMER-CALLABLE",
    re.IGNORECASE,
)
#: `<N> of them are CUSTOMER-CALLABLE` — the older phrasing; still a claim.
_PROSE_CALLABLE = re.compile(
    r"\b([A-Za-z][A-Za-z-]*)\s+of them are customer-callable\b", re.IGNORECASE
)
#: `... are <N> of them` — a later sentence re-asserting the same count. Any
#: sentence that restates the number is a claim, so it is checked too: the
#: `#7956` erratum was exactly a second sentence contradicting the first.
_PROSE_CALLABLE_AGAIN = re.compile(r"\bare\s+([A-Za-z][A-Za-z-]*)\s+of them\b", re.IGNORECASE)

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
    "twenty-six": 26,
    "twenty-seven": 27,
    "twenty-eight": 28,
    "twenty-nine": 29,
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
    census from reproducing its old blindness one level up. The third half is
    the empty-pattern class: a module that walks EVERY relationship type names
    neither the edge nor a constant, so it is found by the shape of its pattern
    (`_may_traverse_every_type`) — which is how `navigation.py` and the backfill
    census were missing from a census that had already declared the class.
    """
    parsed = {file.path: file for file in _literal_files()}
    names = _rel_constant_names()
    for path, source in _sources().items():
        if path in parsed:
            continue
        if any(name in source for name in names) or _may_traverse_every_type(source):
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


def _may_traverse_every_type(source: str) -> bool:
    """Text pre-filter for the empty-pattern class: is there a bracket with no
    type in it? Cheap, and only a filter — `_is_empty_pattern_read` decides.
    """
    return any(":" not in match.group(1) for match in _EMPTY_TRAVERSAL.finditer(source))


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


def _is_empty_pattern_read(text: str) -> bool:
    """Does this Cypher traverse a relationship it does NOT name?

    Same governing-clause rule as `_is_read` — only a `MATCH`/`OPTIONAL MATCH`
    makes it a read — but the subject is the pattern's SHAPE: a bracketed
    traversal whose contents carry no `:` has no type, so it walks every
    relationship type and `aboutObject` is one of them.
    """
    for match in _EMPTY_TRAVERSAL.finditer(text):
        if ":" in match.group(1):
            continue
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
        docstrings = _docstrings(file)
        for node in _roots(file):
            if id(node) in docstrings:
                continue
            candidates = _resolve(node, _scope(file, node), file)
            if not any(_is_empty_pattern_read(text) for text in candidates):
                continue
            line = getattr(node, "lineno", 0)
            for symbol in _site_symbol(node, file):
                sites.setdefault((file.rel, symbol), []).append(line)
    return {key: tuple(sorted(value)) for key, value in sites.items()}


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
def _prose_counts() -> dict[str, tuple[str, ...]]:
    """§13's asserted counts, verbatim, keyed by the UNIT each one counts.

    `sites` / `rows` / `callable_rows` / `callable_sites` are different numbers,
    and the headline used to mix the first and the third under one "of them".
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
    callable_rows = [m.group(1) for m in _PROSE_CALLABLE_ROWS.finditer(text)]
    callable_rows += [m.group(1) for m in _PROSE_CALLABLE.finditer(text)]
    callable_rows += [m.group(1) for m in _PROSE_CALLABLE_AGAIN.finditer(text)]
    return {
        "sites": tuple(m.group(1) for m in _PROSE_SITES.finditer(text)),
        "rows": tuple(m.group(1) for m in _PROSE_ROWS.finditer(text)),
        "callable_rows": tuple(callable_rows),
        "callable_sites": tuple(m.group(2) for m in _PROSE_CALLABLE_ROWS.finditer(text)),
    }


def test_the_table_is_not_vacuous():
    """Every arm below must be able to find something, or all of them pass by
    finding nothing — the failure mode that let four hand audits conclude the
    list was complete."""
    sites = _census()
    empty_pattern = _empty_pattern_sites()
    rows = _declared_rows()
    customer_callable = [row for row in rows if "customer-callable" in row[2].lower()]

    assert len(sites) >= _MIN_SITES, (
        f"the census derived only {len(sites)} read sites from tortoise/ — the "
        "traversal shape changed (or the parser broke) and the completeness arm "
        "would pass vacuously. Sites: "
        + ", ".join(sorted(f"{f}::{s}" for f, s in sites))
    )
    assert len(empty_pattern) >= _MIN_EMPTY_PATTERN, (
        "the empty-pattern walk derived only "
        f"{len(empty_pattern)} sites from tortoise/ — the shape detector broke, "
        "and with it the only arm that can see a reader which names no "
        "relationship type. The defect that produced this arm was exactly this "
        "class going unenumerated."
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
    # The class register is the only hand-maintained list in the file now, so it
    # must not quietly empty out (its members are checked against the code).
    assert EMPTY_PATTERN, (
        "the empty-pattern register is empty — either every member is a §13 row "
        "(delete the register and this arm) or the class's out-of-scope members "
        "were dropped without a replacement, which is how three of them went "
        "missing while every arm stayed green."
    )


def test_the_prose_counts_match_the_table():
    """The NUMBERS are the argument, so they are asserted, not narrated.

    §13 states its own totals in prose, and the KEEP ruling rests on the
    customer-callable one. The arms above guard the **rows**; nothing guarded
    the **numerals**, so a row demoted in the `reach` column left every test
    green while the headline still said SIX. That is the `#7956` erratum's
    direction (the count was under-stated), and it is the error this table
    exists to prevent.

    ⚠️ **Each numeral is checked against its own unit.** The headline used to
    read *"TWENTY read sites, and SIX of them are CUSTOMER-CALLABLE"*: TWENTY
    counted backticked SYMBOLS, SIX counted ROWS, and the six customer-callable
    rows carried nine symbols — so "of them" was false by three, and the arm
    baked the mismatch in by comparing each numeral to a different set. Sites,
    rows, customer-callable rows and the sites inside them are now four
    separate claims, each compared to the set it names.

    Only the numerals are read — never the prose around them — so the sentences
    stay free to be rewritten as long as they stay true.
    """
    counts = _prose_counts()
    rows = _declared_rows()
    declared_sites = {pair for _n, _cell, _reach, pairs in rows for pair in pairs}
    customer_callable = [row for row in rows if "customer-callable" in row[2].lower()]
    callable_sites = {
        pair for _n, _cell, _reach, pairs in customer_callable for pair in pairs
    }

    #: `(key, what it counts, the set it must equal, the sentence form)` — the
    #: four units, so a rewrite cannot state one where the other belongs.
    claims: tuple[tuple[str, str, int, str], ...] = (
        ("sites", "read sites", len(declared_sites), "`<N> read sites`"),
        ("rows", "rows", len(rows), "`<N> rows`"),
        (
            "callable_rows",
            "customer-callable rows",
            len(customer_callable),
            "`<N> of those rows (<M> sites) are CUSTOMER-CALLABLE`",
        ),
        (
            "callable_sites",
            "sites inside the customer-callable rows",
            len(callable_sites),
            "`<N> of those rows (<M> sites) are CUSTOMER-CALLABLE`",
        ),
    )

    problems: list[str] = []
    for key, unit, actual, form in claims:
        tokens = counts[key]
        if not tokens:
            problems.append(
                f"§13 no longer states its {unit} count ({form}), so the number "
                "this arm polices is unguarded again (restore the sentence, or "
                "delete this arm and say why in the docstring)"
            )
            continue
        for token in tokens:
            stated = _numeral(token)
            if stated is None:
                problems.append(
                    f"§13 states `{token}` {unit} and this guard cannot read "
                    "that numeral — spell it out in words or digits"
                )
            elif stated != actual:
                problems.append(
                    f"§13 states `{token}` {unit}; the table has {actual} "
                    f"({form})"
                )

    assert not problems, (
        "the counts §13 asserts do not match the table it asserts them about. "
        "⚠️ The customer-callable number IS the argument for keeping the edge — "
        "a stale numeral here is the ruling resting on an unmeasured claim, and "
        "a numeral checked against the WRONG unit is the same claim with a "
        "cleaner alibi:\n  - "
        + "\n  - ".join(problems)
    )


def test_every_code_site_that_traverses_the_edge_is_declared():
    """COMPLETENESS: both derivations must be declared somewhere.

    This is the arm that no symbol-resolution check can replace — a *missing*
    row has no symbol to fail to resolve. It covers both classes:

    * a reader that NAMES the edge — literally, or through a constant the
      resolver can follow — must be a §13 row;
    * a reader that names NO type at all (`MATCH (n)-[r]->(m)`) must be either a
      §13 row or an `EMPTY_PATTERN` entry saying why it is not an `aboutObject`
      read for a referent. ⚠️ This class used to be ONE declared example, so the
      three further members that exist today — `entityProfile` / `tortoise_traverse`
      (customer-callable) and the backfill census — were absent from the table
      while every arm stayed green.
    """
    declared = {
        pair for _n, _cell, _reach, pairs in _declared_rows() for pair in pairs
    }
    registered = {(filename, _last(symbol)) for filename, symbol, _reason in EMPTY_PATTERN}

    undeclared = []
    for label, sites in (
        ("names the edge", _census()),
        ("names no type (empty pattern)", _empty_pattern_sites()),
    ):
        for (path, symbol), lines in sorted(sites.items()):
            if (path.rsplit("/", 1)[-1], symbol) in declared:
                continue
            if (path, symbol) in registered:
                continue
            undeclared.append((label, path, symbol, lines))

    assert not undeclared, (
        "these sites traverse the `aboutObject` edge and §13's table neither "
        "names them nor declares them out of scope. That table is the evidence "
        "base for the KEEP ruling, and the document's own rule is that the "
        "count IS the argument — so an omitted reader is an unmeasured one "
        "(add the row, with the symbol, to `## 13` of STORAGE-ARCHITECTURE.md, "
        "or — only if it is not an about* read for a referent — add it to "
        "`EMPTY_PATTERN` with the reason):\n  - "
        + "\n  - ".join(
            f"{path}::{symbol} — {label} (lines {lines})"
            for label, path, symbol, lines in undeclared
        )
    )


def test_every_declared_symbol_still_performs_the_read():
    """ROT: a row whose symbol resolves but does not read is a false row.

    Four prior audits failed in this direction too — a symbol that exists, is
    quoted, and is not where the traversal happens — so the check is that the
    symbol is a **site of one of the two derivations**, not merely present in
    the file. A name-only check would pass `_hop_query` (which builds the query)
    for the wrong reason, so both classes count and nothing else does.
    """
    site_symbols: set[tuple[str, str]] = {
        (path.rsplit("/", 1)[-1], symbol) for path, symbol in _census()
    } | {(path.rsplit("/", 1)[-1], symbol) for path, symbol in _empty_pattern_sites()}
    # A reader of the un-derivable shape is declared by name, so it cannot be a
    # derived site — `test_the_undeclarable_shape_is_declared_by_name` owns it
    # (it checks the symbol exists, is a row, and is still not derived).
    declared_by_name = {
        (filename.rsplit("/", 1)[-1], _last(symbol))
        for filename, symbol, _why in UNDERIVABLE_CALL_TIME
    }

    problems: list[str] = []
    for number, _site_cell, _reach, pairs in _declared_rows():
        for key in pairs:
            if key in site_symbols or key in declared_by_name:
                continue
            problems.append(
                f"row {number}: `{key[1]}` ({key[0]}) is declared as a reader but no "
                "Cypher in it traverses `aboutObject` (by name or through an "
                "empty pattern)"
            )

    assert not problems, (
        "§13's table names these as readers of the `aboutObject` edge, and they "
        "are not — a re-verifier following the row finds no read and concludes "
        "the passage is wrong (the exact failure EXTRACTOR-V4-ARCHITECTURE.md's "
        "citation guard exists for, one document over):\n  - "
        + "\n  - ".join(problems)
    )


def test_the_empty_pattern_class_is_declared():
    """The class that names NO type is DERIVED, and every member is declared.

    Two directions, because each one was a real defect:

    * **every derived member is declared** — a NEW empty-pattern reader cannot
      appear silently. Declaring one example by name (`_neighbors`) is what let
      `entityProfile`/`tortoise_traverse` and the backfill census stay absent.
    * **every registered member still derives** — a register entry whose reader
      stopped traversing the pattern (renamed, rewritten to name types) is rot,
      and a stale entry is what makes the next reader's absence invisible.
    """
    derived = _empty_pattern_sites()
    row_symbols = {
        pair for _n, _cell, _reach, pairs in _declared_rows() for pair in pairs
    }
    derived_keys = set(derived)

    missing = [
        f"{filename}::{symbol} — {reason}"
        for filename, symbol, reason in EMPTY_PATTERN
        if (filename, _last(symbol)) not in derived_keys
    ]
    assert not missing, (
        "these `EMPTY_PATTERN` entries are no longer empty-pattern readers of "
        "the edge, so the register has rotted (re-verify each: a renamed symbol "
        "or a query that now names its types):\n  - " + "\n  - ".join(missing)
    )

    registered = {(filename, _last(symbol)) for filename, symbol, _reason in EMPTY_PATTERN}
    about_reads = sorted(
        key
        for key in derived_keys
        if (key[0].rsplit("/", 1)[-1], key[1]) in row_symbols and key not in registered
    )
    assert len(about_reads) >= 3, (
        "the empty-pattern class has fewer than three members declared as §13 "
        "rows — the class's about*-relevant readers are `_neighbors`,\n"
        "  `_hop_query` (entityProfile / tortoise_traverse) and the backfill "
        "census; if one of them stopped deriving, the shape detector broke and "
        "this arm would pass over a missing row. Derived as rows: "
        + ", ".join(f"{f}::{s}" for f, s in about_reads)
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
        "parameter default",
        'def read(g, rel="aboutObject"):\n'
        '    g.query(f"MATCH (n)-[:{rel}]->(m) RETURN n")\n',
        True,
    ),
    (
        "MERGE writer",
        'def write(g):\n    g.query("MERGE (n)-[:aboutObject]->(m) RETURN n")\n',
        False,
    ),
)


def test_the_undeclarable_shape_is_declared_by_name():
    """The class NO census can derive is declared, and the declaration is checked.

    A relationship name assembled entirely from a call-time value (a dict lookup,
    an instance attribute) resolves to nothing — no literal, no constant, no
    empty pattern — so it cannot be derived and must be declared by name in §13.
    Both directions are checked, because each has already failed once:

    * **every entry is a §13 row** — the previous revision of §13 said this class
      was "declared by name instead" while it declared nothing at all, and two
      about*-specific readers of the shape (`link_entity`, `bind_point_subjects`)
      were in neither the table nor any list.
    * **every entry is still un-derivable** — if the resolver learns to follow
      it, the entry is stale and the class statement is wrong.
    """
    rows = {pair for _n, _cell, _reach, pairs in _declared_rows() for pair in pairs}
    derived = set(_census()) | set(_empty_pattern_sites())

    problems: list[str] = []
    for filename, symbol, why in UNDERIVABLE_CALL_TIME:
        target = ROOT / filename
        if not target.exists() or symbol not in target.read_text(encoding="utf-8"):
            problems.append(
                f"`{symbol}` no longer exists in {filename} — re-verify the row "
                "and this declaration"
            )
            continue
        if (filename.rsplit("/", 1)[-1], _last(symbol)) not in rows:
            problems.append(
                f"{filename}::{symbol} reads the edge ({why}) and no §13 row "
                "names it — the class is declared here precisely because nothing "
                "derives it, so the row is the only place a re-verifier can see it"
            )
        if (filename, _last(symbol)) in derived:
            problems.append(
                f"{filename}::{symbol} is DECLARED as un-derivable and one of "
                "the censuses now derives it — delete the entry and keep the row"
            )

    assert not problems, (
        "the shape that names no relationship type at all is the one shape only "
        "a declaration can cover, so the declaration must be complete and must "
        "not be stale:\n  - " + "\n  - ".join(problems)
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


#: Synthetic sources for the empty-pattern arm: shapes that DO name no type, and
#: shapes that look similar but must not count (a named type is the named
#: census's business; a `MERGE` writes; a docstring only quotes Cypher).
_EMPTY_PATTERN_CASES: tuple[tuple[str, str, bool], ...] = (
    (
        "outbound empty pattern",
        'def read(g):\n    g.query("MATCH (n)-[r]->(m) RETURN type(r)")\n',
        True,
    ),
    (
        "inbound empty pattern",
        'def read(g):\n    g.query("MATCH (n)<-[r]-(m) RETURN type(r)")\n',
        True,
    ),
    (
        "variable-length empty pattern",
        'def read(g):\n    g.query("MATCH (n)-[r*1..2]->(m) RETURN type(r)")\n',
        True,
    ),
    (
        "f-string with an empty pattern",
        'def read(g, label):\n'
        '    g.query(f"MATCH (n:{label})-[r]->(m) RETURN type(r)")\n',
        True,
    ),
    (
        "named type (the named census's job, not this class)",
        'def read(g):\n    g.query("MATCH (n)-[r:aboutObject]->(m) RETURN n")\n',
        False,
    ),
    (
        "MERGE writer",
        'def write(g):\n    g.query("MERGE (n)-[r]->(m)")\n',
        False,
    ),
    (
        "a docstring quoting the pattern is not a site",
        'def read(g):\n    """Tried `MATCH (n)<-[r]-(m)`; it scanned."""\n'
        "    return None\n",
        False,
    ),
)


def _empty_pattern_sites_in(file: _File) -> set[tuple[str, int]]:
    """`_empty_pattern_sites()` restricted to one (usually synthetic) module."""
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


def test_the_empty_pattern_class_derivation_holds():
    """The MECHANISM of the second class, on synthetic sources.

    The class is only as good as its shape detector: if the pattern match
    regressed, `_empty_pattern_sites()` would return nothing, the three §13 rows
    for its members would become "declared but not derived", and — worse — a new
    member would be silently acceptable. These cases pin both directions on
    sources that do not depend on today's tree, including the two near-misses
    that must NOT count (a named type, and a docstring that only quotes Cypher).
    """
    missing = []
    for label, source, should_derive in _EMPTY_PATTERN_CASES:
        derived = _empty_pattern_sites_in(_File(TORTISE / "<synthetic>", "<synthetic>", source))
        if bool(derived) is not should_derive:
            missing.append(
                f"{label}: expected {'a site' if should_derive else 'no site'}, "
                f"derived {sorted(derived)}"
            )
    assert not missing, (
        "the empty-pattern derivation lost a shape (or started counting one it "
        "must not), so a reader that names no type would pass undeclared:\n  - "
        + "\n  - ".join(missing)
    )


#: The sibling paragraph that narrates the census in `EXTRACTOR-V4-ARCHITECTURE.md`
#: (its opening line — the paragraph itself is one long line).
_EXTRACTOR_PASSAGE = "2. **Should `aboutObject` be a stored edge or a derived join?**"

#: A `file.py:NNNN` line hint. The census prose must carry NONE: a line number is
#: the one citation shape this guard cannot check (asserting it would red on every
#: unrelated edit), so the honest form is to omit it, as §13 already does.
_LINE_HINT = re.compile(r"\b[A-Za-z0-9_/]+\.py:\d[\d+-]*")

#: The sentence that declares the empty-pattern class *by its rows*.
_EMPTY_PATTERN_PROSE = re.compile(
    r"empty relationship pattern\*\*.{0,160}?\brows?\s+"
    r"((?:\d+)(?:[,\s]+(?:and\s+)?\d+)*)"
)

#: A backticked token in a `reach` cell that claims to be a code symbol.
_REACH_SYMBOL = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


@lru_cache(maxsize=1)
def _census_prose() -> tuple[tuple[str, int, str], ...]:
    """Every line that NARRATES the census: §13's bullet and the sibling paragraph.

    The census is a claim about the CODE and it is written twice — once as the
    table, once as prose around it. Only the table was parsed, so the prose could
    carry a stale line number, an under-counted class and a symbol that does not
    exist while every arm stayed green: a PASS about a false artifact, which is
    the failure mode this whole instrument exists to end.
    """
    lines: list[tuple[str, int, str]] = []
    inside = False
    for number, line in enumerate(STORAGE.read_text(encoding="utf-8").splitlines(), start=1):
        if not inside:
            inside = line.startswith(_BULLET_START)
            continue
        if line.startswith("- **") or line.startswith("---"):
            break
        lines.append(("STORAGE §13", number, line))
    for number, line in enumerate(
        EXTRACTOR.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if line.startswith(_EXTRACTOR_PASSAGE):
            lines.append(("EXTRACTOR-V4 (the aboutObject decision paragraph)", number, line))
    return tuple(lines)


def test_the_census_prose_carries_no_line_citations():
    """The census prose must state SYMBOLS, never `file.py:NNNN`.

    §13 states the rule for the whole census — *the symbols are authoritative and
    the line numbers are deliberately omitted*, because they rotted between
    `c79ba1cf2` and the current head — and then the sibling paragraph in
    `EXTRACTOR-V4-ARCHITECTURE.md` carried fifteen hints and said they were "the
    convenience, not the authority". At the head that rewrote it, six of the
    fifteen pointed at unrelated code (a comment inside another function, a
    `return` statement, a bare `*,` in a signature, a docstring line), and the
    same numbers were already different again one merge later. A re-verifier
    following one lands in unrelated code and concludes the passage is wrong.

    A line number is not something this guard can keep true — asserting it would
    red on every unrelated edit — so the claim is REMOVED rather than trusted.
    The symbols stay, and they are what the other arms resolve.
    """
    prose = _census_prose()
    assert prose, (
        "the census prose was not found at all, so this arm passes vacuously — "
        "`_census_prose()` located neither §13's bullet nor the sibling "
        "paragraph; re-point the markers at wherever the census is narrated"
    )
    assert any(where.startswith("EXTRACTOR") for where, _number, _line in prose), (
        "the sibling passage in `EXTRACTOR-V4-ARCHITECTURE.md` was not located, "
        "so half of this arm is checking nothing — the paragraph's opening line "
        "moved or was re-worded, and the fifteen line hints this arm exists for "
        "live in it: re-point `_EXTRACTOR_PASSAGE` at it"
    )
    offenders = [
        f"{where} line {number}: `{hint}`"
        for where, number, line in _census_prose()
        for hint in (m.group(0) for m in _LINE_HINT.finditer(line))
    ]
    assert not offenders, (
        "these `file.py:NNNN` hints are back in the census prose. Each one is a "
        "claim that rots on every edit to the file it cites — and the passage "
        "this arm was written for already had six of seven pointing at "
        "unrelated code — so state the symbol and drop the number (see §13's "
        "own sentence: the symbols are authoritative and the line numbers are "
        "deliberately omitted):\n  - " + "\n  - ".join(offenders)
    )


def test_the_prose_names_every_row_in_the_empty_pattern_class():
    r"""The class sentence is a prose RESTATEMENT of a derived set, so compare it.

    §13 declares the empty-pattern class by naming its rows: *"an empty
    relationship pattern (`MATCH (n)-[r]->(m)`, rows 3 and 17)"*. Row 18 (the
    backfill census, `OPTIONAL MATCH (o)<-[r]-()`) is the same class, and the
    guard's own docstring said so — so the sentence under-counted its own
    members. That is an under-counted declaration of the exact kind the table
    itself was four times corrected for, one level up, and it is what a
    re-verifier reads FIRST when told the class exists.

    The asserted set is derived (rows whose symbols are empty-pattern reads at
    this head), never a second hand list, so the sentence and the class cannot
    drift apart again.

    ⚠️ The derived set is restricted to the readers the shape walk is the ONLY
    route to — an empty-pattern reader whose about\*-relevance is gated by a
    relationship constant (`supersede_point`, `_preview_supersede`) is reached by
    the NAME census as well, so the sentence is not about it. That restriction is
    derived, not asserted by hand: the rows here are the empty-pattern rows the
    named census does not also derive.
    """
    named_keys = {(path.rsplit("/", 1)[-1], symbol) for path, symbol in _census()}
    derived_keys = {
        (path.rsplit("/", 1)[-1], symbol) for path, symbol in _empty_pattern_sites()
    }
    shape_only = derived_keys - named_keys
    derived_rows = {
        number
        for number, _site_cell, _reach, pairs in _declared_rows()
        if any(pair in shape_only for pair in pairs)
    }

    prose_rows: set[int] = set()
    found = False
    for _where, _number, line in _census_prose():
        for match in _EMPTY_PATTERN_PROSE.finditer(line):
            found = True
            prose_rows.update(int(token) for token in re.findall(r"\d+", match.group(1)))

    assert found, (
        "the sentence that declares the empty-pattern class by its rows is gone "
        "(or rewritten out of the shape this arm reads), so nothing says which "
        "rows belong to the class and a new member's row need not appear in it — "
        "re-word the sentence to name the rows after an `empty relationship "
        "pattern`, or delete this arm and move the declaration somewhere it can "
        "be checked"
    )
    assert prose_rows == derived_rows, (
        "the empty-pattern class's declared rows and the class derived from the "
        "code disagree. The sentence under-counted its own members once already "
        "(it said rows 3 and 17 while the backfill census, row 18 — "
        "`OPTIONAL MATCH (o)<-[r]-()` — is the same shape), which is the same "
        "direction as the table's four corrections and reads as authoritative to "
        "the next re-verifier. Rows only the shape walk reaches: "
        f"{sorted(derived_rows)}; rows the sentence names: {sorted(prose_rows)}"
    )


def test_the_reach_column_names_only_symbols_that_exist():
    """Every backticked symbol in a `reach` cell must exist in the package.

    The other arms resolve the `site` column, so `reach` was unguarded prose — and
    it cited `QueryOptions`, a name defined nowhere in `tortoise/` (the flag it
    describes is a keyword parameter of `tortoise_fts_query`). A non-existent
    symbol in the column that says WHO can reach a reader is the same defect as a
    non-existent row: a re-verifier looks for it, finds nothing, and distrusts
    the passage. This is deliberately a *presence* check, not a reachability
    claim — the prose around the symbol carries the reasoning.
    """
    corpus = "\n".join(_sources().values())

    checked = 0
    problems: list[str] = []
    for number, _site_cell, reach, _pairs in _declared_rows():
        for token in re.findall(r"`([^`]+)`", reach):
            token = token.strip()
            if not _REACH_SYMBOL.match(token):
                continue
            checked += 1
            if token not in corpus:
                problems.append(
                    f"row {number}: `{token}` is cited in `reach` and appears "
                    "nowhere in `tortoise/`"
                )

    assert checked >= 3, (
        f"only {checked} backticked symbols found in any `reach` cell — the "
        "column's shape changed and this arm would pass vacuously"
    )
    assert not problems, (
        "these `reach` cells cite symbols that do not exist in the package, so a "
        "re-verifier following the row finds nothing and distrusts the passage "
        "(correct the reference to the real symbol, or drop the backticks if it "
        "is prose):\n  - " + "\n  - ".join(problems)
    )
