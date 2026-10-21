"""#7948 — the §13 `aboutObject` read-site enumeration must be EXECUTABLE, not prose.

THE DEFECT THIS PINS
--------------------
`docs/architecture/STORAGE-ARCHITECTURE.md` §13 carries the evidence the
owner's 2026-09-23 **KEEP the stored edges** ruling rests on: an enumeration
of the code that READS the `aboutObject` edge, plus the count the document
itself calls decisive — *"the count is the whole argument"*.

That enumeration is hand-maintained prose, and it has been wrong three times
already:

  * #7948's body asserted the measurement did not exist at all;
  * review finding B2.7 enumerated 22 files and **two** customer-callable
    sites (`REVIEW-CONSOLIDATED-2026-09-23.md:122` — itself corrected there);
  * the `#7956` correction (PR `#7986`) applied in §13 found **four**.

Re-derived against the code at `26aff163c`, the table was stale again, in the
two directions that matter.

**The reader SYMBOLS had rotted** — the table's defence is *"the symbols are
authoritative, the line number is a convenience"*, and nothing checked that:

  * row 2 named `topic_summarize`, which does not read the edge; the reader
    is `_retrieve_topic_neighborhood`, which builds the edge NAME at run time
    (`edge_type = "aboutSubject" if … else "aboutObject"`) so no grep sees it;
  * row 4 named no symbol at all — `search(order_by="graph")` is a DOOR — and
    its two line numbers point at unrelated code (`ranking.py:714` is
    `eid = row[0]`); the reader is `GraphRanker._fetch_event_signals`;
  * row 8 named `collect_slices`, which never mentions the edge: it delegates
    the read to `spine_rows`, which lives inside `docker_walker_port`;
  * row 9 named `build_subgraph`, which reads none of it — the reads are the
    module constants `_CYPHER_ABOUT` / `_CYPHER_SIBLINGS`.

**The list was still incomplete, and the missing part was the load-bearing
kind** — the customer-callable count was **8 sites across 5 doors, not 4**:

  * the entire **`recall_state`** path was absent, and it reads the edge on
    **four** paths: `StateRanker._fetch_point_signals` (a literal alternation),
    `StateRanker._fetch_object_signals`, `TortoiseSDK._state_related_objects`
    and `TortoiseSDK._state_related_points` — the last three name the edge in
    an `about*` alternation CONSTANT (`ABOUT_EDGE_TYPES`,
    `TortoiseSDK._ABOUT_TYPES`), so neither the literal `aboutObject` nor a
    bare `MATCH (n)-[r]->(m)` appears in them. Same blind spot that made
    `recall_subgraph`'s every-relationship walk the site no grep can find,
    one layer further out;
  * `session_schema.audit_session_retrofit` (ops audit),
    `projection/edges._EdgeHandlers._count_about_out` (the `#8398` restore
    path) and `TortoiseSDK.supersede_point` (the supersede rewire) were
    unnamed;
  * the counts the document states elsewhere had drifted too: *"36 test files
    assert the edge at the pin"* measured 59 at `26aff163c`.

The two documents that carry this census (§13 and `EXTRACTOR-V4-ARCHITECTURE.md`
§13.8) also disagreed with each other — the sibling still said FOUR — which is
how an under-count survives a review: each copy looks confirmed by the other.

WHAT THIS PINS
--------------
1. `test_every_row_names_a_symbol_that_exists` — each row's
   `` `module.py::symbol` `` resolves in the code (dotted: class attribute).
   A rename or removal reds here instead of leaving the evidence stale.
2. `test_every_named_reader_still_reads_the_edge` — the named symbol really
   reads the edge, by any of the three ways a reader can name it: a Cypher
   **read** clause (`MATCH`/`OPTIONAL MATCH`) naming it literally; a carrier
   CONSTANT whose value names it; or — for the two readers that COMPUTE the
   edge name — the code fact that makes them reads (`rel_pattern` empty for
   `completeness="full"`; `` -[:{edge_type}]-> `` with `edge_type`'s
   else-branch `"aboutObject"`).
3. `test_the_computed_name_readers_are_still_complete_reads` — the
   every-relationship walk keeps its empty-pattern default, which is what
   makes `recall_subgraph` a reader at all.
4. `test_no_module_reads_the_edge_without_a_row` — the census half. Every
   module under `tortoise/` containing a read clause that names the edge must
   be named by some row.
5. `test_the_prose_counts_match_the_table` — the arithmetic ("TWENTY read
   sites … EIGHT of them are CUSTOMER-CALLABLE") equals the table. This is
   the figure that was wrong twice, and it is the figure the ruling turns on.
6. `test_every_carrier_constant_a_row_reads_through_still_names_the_edge` —
   the constant IS the read for rows 17–20: if `aboutObject` leaves
   `ABOUT_EDGE_TYPES` / `_ABOUT_TYPES` / `SUPERSEDE_STRUCTURAL_RELS`, the row
   still resolves while the site stops reading the edge.
7. `test_every_customer_callable_row_reaches_a_declared_door` — each
   customer-callable row names a door that exists: ``SDK `<method>` `` must be
   a public `TortoiseSDK` method, ``MCP `<tool>` `` a registered
   `ToolDefinition`. That is what makes "customer-callable" a claim about the
   product surface rather than an adjective.
8. `test_the_extractor_document_agrees_on_the_count` — the sibling document
   states the SAME count and names the state-recall readers; the two copies
   cannot diverge again.

⚠️ **What this deliberately does NOT claim.** The census is a LOWER BOUND: it
sees a literal relationship pattern naming the edge, so a reader that
computes or carries the name (`_neighbors`, `_retrieve_topic_neighborhood`,
rows 17–20) is invisible to it and is pinned by (2)/(6) instead. Line NUMBERS
are not asserted — the symbol is authoritative and a symbol that merely moved
passes. And the enumeration is of READERS of the edge as data; writers that
MERGE it are not rows here.

Static, stdlib-only, no network / DB / graph.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "architecture" / "STORAGE-ARCHITECTURE.md"
PKG = ROOT / "tortoise"

#: The edge named inside a Cypher relationship bracket — `[:aboutObject]`,
#: `[r:aboutObject]`, `[:aboutSubject|aboutObject]`, `[r1:aboutObject]`.
_EDGE_PATTERN = re.compile(r"\[[^\]\n]{0,60}aboutObject[^\]\n]{0,60}\]")

#: Clauses that decide whether the pattern is read or written.
_CLAUSE = re.compile(
    r"\b(OPTIONAL\s+MATCH|MATCH|MERGE|CREATE|SET|DELETE|DETACH)\b", re.I
)
_READ_CLAUSES = {"MATCH", "OPTIONAL MATCH"}

#: A table row: `| 3 | `module.py::Symbol.attr` | reads for | **reach** |`.
_ROW = re.compile(
    r"^\s*\|\s*(\d+)\s*\|\s*`([\w/]+\.py)::([\w.]+)`\s*\|(.*?)\|(.*?)\|\s*$", re.M
)

#: The declared reach vocabulary. A row's reach cell must OPEN with one of
#: these; free prose may follow.
REACH_VOCAB: tuple[str, ...] = (
    "customer-callable",
    "write path",
    "journal replay",
    "ops audit",
    "off by default",
    "eval harness",
)

#: Readers whose edge name is COMPUTED, so no literal pattern can carry it.
#: Each maps to the code facts that make the read real — asserted literally and
#: IN THE READER'S OWN SPAN, because these rows are the table's sharpest claim
#: (a grep cannot find them, which is precisely why the enumeration was
#: understated twice). A module-wide substring test would let the fact survive
#: somewhere else in the file; the span is what makes it this reader's read.
_NON_LITERAL: dict[str, tuple[str, ...]] = {
    "ranking.py::SubgraphExpander._neighbors": (
        'f"[r{rel_pattern}]"',
    ),
    "topic_summarization.py::_retrieve_topic_neighborhood": (
        'edge_type = "aboutSubject" if entity_label == "Subject" else "aboutObject"',
        "MATCH (p:Point)-[:{edge_type}]->",
    ),
    # The `tortoise_recall(mode="state")` readers: the predicate is the `about*`
    # ALTERNATION, so `aboutObject` is read as one member and the literal string
    # never appears at the site. This is the form that under-counted the
    # customer-callable surfaces three times (#7956's erratum, B2.7, #7948).
    "ranking.py::StateRanker._fetch_object_signals": (
        'about_types = "|".join(ABOUT_EDGE_TYPES)',
        '"OPTIONAL MATCH (p)-[a:" + about_types + "]->(o) "',
    ),
    "sdk.py::TortoiseSDK._state_related_objects": (
        '"MATCH (n:Point)-[a:" + self._ABOUT_TYPES + "]->(t) "',
    ),
    "sdk.py::TortoiseSDK._state_related_points": (
        '"MATCH (p:Point)-[a:" + self._ABOUT_TYPES + "]->(o:Object) "',
    ),
}

#: Facts outside the symbol that make the two computed-name readers complete
#: reads: the empty `rel_pattern` for the default completeness mode.
_NON_LITERAL_SIBLINGS: dict[str, tuple[str, ...]] = {
    "ranking.py::SubgraphExpander._neighbors": (
        'rel_pattern = ":IMPL|NAND" if completeness == "core" else ""',
        'completeness: str = "full"',
    ),
}

_NUMBER_WORDS = {
    word: value
    for value, word in enumerate(
        (
            "ZERO",
            "ONE",
            "TWO",
            "THREE",
            "FOUR",
            "FIVE",
            "SIX",
            "SEVEN",
            "EIGHT",
            "NINE",
            "TEN",
            "ELEVEN",
            "TWELVE",
            "THIRTEEN",
            "FOURTEEN",
            "FIFTEEN",
            "SIXTEEN",
            "SEVENTEEN",
            "EIGHTEEN",
            "NINETEEN",
            "TWENTY",
        )
    )
}


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _section_13() -> str:
    """§13 only — the rulings section, so a stray table elsewhere cannot pass."""
    text = _doc()
    start = text.index("## 13. Filed from this document")
    end = text.index("## 14. What this document does not decide")
    return text[start:end]


def _rows() -> list[dict[str, object]]:
    rows = [
        {
            "n": int(m.group(1)),
            "module": m.group(2),
            "symbol": m.group(3),
            "reads_for": m.group(4).strip(),
            "reach": m.group(5).strip(),
        }
        for m in _ROW.finditer(_section_13())
    ]
    assert rows, (
        "§13 carries no `module.py::symbol` read-site rows. The table's rows are "
        "the ruling's evidence and the guard parses them; restoring them in the "
        "old `file.py:NNNN` form silently disables this guard."
    )
    return rows


def _reach(row: dict[str, object]) -> str:
    cell = re.sub(r"[*`]", "", str(row["reach"])).strip().lower()
    for token in REACH_VOCAB:
        if cell.startswith(token):
            return token
    raise AssertionError(
        f"row {row['n']} ({row['module']}::{row['symbol']}) has reach "
        f"{row['reach']!r}, which does not open with a declared class "
        f"({', '.join(REACH_VOCAB)})"
    )


def _module_path(rel: str) -> Path:
    path = PKG / rel
    assert path.exists(), f"§13 names {rel}, which does not exist under tortoise/"
    return path


def _module_tree(path: Path) -> ast.Module:
    return _parsed(str(path))


@functools.cache
def _parsed(path: str) -> ast.Module:
    return ast.parse(Path(path).read_text(encoding="utf-8"))


def _bound_name(node: ast.AST) -> str | None:
    """The name a definition or assignment binds (`C.attr` resolves one level)."""
    name = getattr(node, "name", None)
    if isinstance(name, str):
        return name
    target = None
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
    elif isinstance(node, ast.AnnAssign):
        target = node.target
    if isinstance(target, ast.Name):
        return target.id
    return None


def _span(tree: ast.Module, dotted: str) -> tuple[int, int] | None:
    """Resolve `name` or `Class.attr` to its source span, or None."""
    head, _, tail = dotted.partition(".")
    for node in tree.body:
        if _bound_name(node) != head:
            continue
        if not tail:
            return (node.lineno, node.end_lineno or node.lineno)
        if not isinstance(node, ast.ClassDef):
            continue
        for inner in node.body:
            if _bound_name(inner) == tail:
                return (inner.lineno, inner.end_lineno or inner.lineno)
    return None


def _reads_naming_the_edge(tree: ast.Module, lo: int, hi: int) -> bool:
    """True when a Cypher READ clause inside [lo, hi] names the edge literally.

    Read vs write is decided by the LAST clause preceding the pattern inside
    the same string literal, so `MATCH … MERGE (p)-[:aboutObject]->(o)` is a
    WRITE (the MERGE is what the pattern belongs to) and
    `MATCH (e) OPTIONAL MATCH (e)-[:aboutObject]->(o)` is a READ.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if not lo <= getattr(node, "lineno", 0) <= hi:
            continue
        match = _EDGE_PATTERN.search(node.value)
        if match is None:
            continue
        clauses = _CLAUSE.findall(node.value[: match.start()])
        if clauses and " ".join(clauses[-1].upper().split()) in _READ_CLAUSES:
            return True
    return False


@functools.cache
def _carrier_constants(rel: str) -> dict[str, str]:
    # qualified name -> the bare name its readers use
    """module/class-level constants whose SOURCE names the edge.

    A reader can name the edge through one of these instead of in its query —
    `ABOUT_EDGE_TYPES`, `TortoiseSDK._ABOUT_TYPES`, `SUPERSEDE_STRUCTURAL_RELS`
    and the replay families all do. That is how three `recall_state` reads and
    the whole supersede path stayed invisible to a literal census, and it is
    the blind spot this helper closes: the constant's MEMBERSHIP is what makes
    those readers reads, so it is asserted, not assumed.
    """
    path = _module_path(rel)
    text = path.read_text(encoding="utf-8")
    tree = _parsed(str(path))
    carriers: dict[str, str] = {}

    def scan(body, prefix: str = "") -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                scan(node.body, prefix=f"{node.name}.")
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                name = _bound_name(node)
                if name is None:
                    continue
                segment = ast.get_source_segment(text, node) or ""
                if "aboutObject" in segment:
                    carriers[prefix + name] = name
    scan(tree.body)
    return carriers


def _reads_via_carrier(rel: str, source: str) -> list[str]:
    """The edge-name carriers this symbol's source references in a query."""
    if "MATCH" not in source.upper():
        return []
    return sorted(
        qualified
        for qualified, bare in _carrier_constants(rel).items()
        if re.search(rf"\b{re.escape(bare)}\b", source)
    )


@functools.cache
def _carrier_shaped_constants(rel: str) -> dict[str, tuple[str, ...]]:
    """module/class-level constants that can be SPLICED INTO a rel pattern.

    Two shapes qualify: a rel-type list whose elements are all string literals
    (`ABOUT_EDGE_TYPES`, `SUPERSEDE_STRUCTURAL_RELS`) and an `a|b|c`
    alternation string (`TortoiseSDK._ABOUT_TYPES`). Both are what a reader
    interpolates into `-[a:<value>]->`, which is exactly why the edge name can
    live in the constant and nowhere in the query.

    ⛔ Deliberately NOT filtered by `aboutObject`. `_carrier_constants()`
    selects only constants whose source ALREADY contains the edge name, so
    asserting that a selected constant contains it can never fail — removing
    `aboutObject` from `ranking.ABOUT_EDGE_TYPES` left all eight tests green,
    while the mutation the PR body claims reds the carrier test. Candidate
    shape here is decided by the VALUE's form plus the `about*` family prefix,
    both of which survive `aboutObject` itself leaving the constant; membership
    is then asserted by the caller, where it can actually fail.
    """

    def splices(node: ast.AST | None) -> tuple[str, ...] | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return tuple(node.value.split("|")) if "|" in node.value else None
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            elements: list[str] = []
            for element in node.elts:
                if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
                    return None
                elements.append(element.value)
            return tuple(elements)
        return None

    path = _module_path(rel)
    tree = _parsed(str(path))
    carriers: dict[str, tuple[str, ...]] = {}

    def scan(body, prefix: str = "") -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                scan(node.body, prefix=f"{node.name}.")
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                name = _bound_name(node)
                if name is None:
                    continue
                elements = splices(node.value)
                if not elements or not any(e.startswith("about") for e in elements):
                    continue
                carriers[prefix + name] = elements

    scan(tree.body)
    return carriers


def _modules_reading_the_edge() -> dict[str, list[int]]:
    """Census: module → line numbers of each literal Cypher READ of the edge."""
    found: dict[str, list[int]] = {}
    for path in sorted(PKG.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        # Cheap pre-filter: a module that never writes the edge inside a
        # relationship bracket cannot contain a literal read of it.
        if not _EDGE_PATTERN.search(text):
            continue
        tree = _parsed(str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            match = _EDGE_PATTERN.search(node.value)
            if match is None:
                continue
            clauses = _CLAUSE.findall(node.value[: match.start()])
            if clauses and " ".join(clauses[-1].upper().split()) in _READ_CLAUSES:
                found.setdefault(str(path.relative_to(PKG)), []).append(
                    getattr(node, "lineno", 0)
                )
    return found


def _words(cell: str) -> int:
    cell = cell.strip().upper()
    if cell.isdigit():
        return int(cell)
    assert cell in _NUMBER_WORDS, f"{cell!r} is not a spelled number this guard knows"
    return _NUMBER_WORDS[cell]


# ── 1. every row names a symbol that exists ────────────────────────────────


def test_every_row_names_a_symbol_that_exists():
    missing = []
    for row in _rows():
        tree = _module_tree(_module_path(str(row["module"])))
        if _span(tree, str(row["symbol"])) is None:
            missing.append(f"row {row['n']}: {row['module']}::{row['symbol']}")
    assert not missing, (
        "§13's read-site table names symbols that do not exist at HEAD:\n  "
        + "\n  ".join(missing)
        + "\nThe document's rule is that the SYMBOL is authoritative, so a row "
        "whose symbol cannot be resolved is a stale row: re-derive it from the "
        "code rather than moving the line number."
    )


# ── 2. every named reader still reads the edge ─────────────────────────────


def test_every_named_reader_still_reads_the_edge():
    stale = []
    for row in _rows():
        key = f"{row['module']}::{row['symbol']}"
        module_path = _module_path(str(row["module"]))
        tree = _module_tree(module_path)
        span = _span(tree, str(row["symbol"]))
        if span is None:
            stale.append(f"row {row['n']}: {key} — symbol does not resolve")
            continue
        if _reads_naming_the_edge(tree, span[0], span[1]):
            continue
        span_text = "\n".join(
            module_path.read_text(encoding="utf-8").splitlines()[span[0] - 1 : span[1]]
        )
        # A computed-name row is decided by ITS OWN FACTS, IN ITS OWN SPAN,
        # BEFORE the carrier fallback gets a chance to short-circuit it.
        #
        # Order matters, and it was wrong: `_fetch_object_signals` sits in a
        # module whose `ABOUT_EDGE_TYPES` constant carries the edge name, so
        # the carrier branch went green on a name that merely APPEARS in the
        # symbol — two unrelated `MATCH (o:Object)` / `ABOUT_EDGE_TYPES` lines
        # were enough. The consuming line
        # (`"OPTIONAL MATCH (p)-[a:" + about_types + "]->(o) "`) could be
        # deleted and all eight tests stayed green, leaving row 17 naming code
        # that no longer reads the edge. The facts are the read here, so they
        # are asserted first, and only inside the span — a fact found elsewhere
        # in the module is not evidence about THIS reader.
        if key in _NON_LITERAL:
            missing = [fact for fact in _NON_LITERAL[key] if fact not in span_text]
            if missing:
                stale.append(
                    f"row {row['n']}: {key} — computed-name reader lost the "
                    f"fact that makes it a read: {missing!r}"
                )
            continue
        if _reads_via_carrier(str(row["module"]), span_text):
            continue
        stale.append(
            f"row {row['n']}: {key} — the named symbol does not read the edge"
        )
    assert not stale, (
        "§13's table names readers that no longer read `aboutObject`:\n  "
        + "\n  ".join(stale)
        + "\nA consumer-callable surface listed here is the ruling's argument; a "
        "row pointing at code that does not read the edge is how the enumeration "
        "was wrong before (#7948 body, B2.7, #7956)."
    )


def test_the_computed_name_readers_are_still_complete_reads():
    """The two rows no grep can find must keep the fact that makes them reads."""
    ranking = (PKG / "ranking.py").read_text(encoding="utf-8")
    for fact in _NON_LITERAL_SIBLINGS["ranking.py::SubgraphExpander._neighbors"]:
        assert fact in ranking, (
            "`recall_subgraph`'s default `completeness=\"full\"` is what makes "
            "`SubgraphExpander._neighbors` read `aboutObject` without naming it. "
            f"{fact!r} is gone, so the row's reach claim must be re-derived."
        )


# ── 3. no module reads the edge without a row ──────────────────────────────


def test_no_module_reads_the_edge_without_a_row():
    named = {str(row["module"]) for row in _rows()}
    census = _modules_reading_the_edge()
    assert census, (
        "the census found NO literal Cypher read of `aboutObject` anywhere under "
        "tortoise/ — the pattern or the clause rule was changed, and this guard "
        "has stopped measuring anything."
    )
    unnamed = sorted(set(census) - named)
    assert not unnamed, (
        "these modules READ the `aboutObject` edge and are named by no §13 row:\n  "
        + "\n  ".join(f"{module}  (lines {census[module]})" for module in unnamed)
        + "\n§13's list is the evidence the KEEP ruling rests on; an unnamed "
        "reader is the drift B2.7 and #7956 both under-reported."
    )


# ── 4. the prose counts equal the table ────────────────────────────────────


def test_the_prose_counts_match_the_table():
    section = _section_13()
    rows = _rows()
    sites = re.search(r"\b([A-Z]+|\d+) read sites\b", section)
    assert sites, "§13 must state how many read sites it enumerates"
    callable_sites = re.search(
        r"\b([A-Z]+|\d+) of them are CUSTOMER-CALLABLE\b", section
    )
    assert callable_sites, "§13 must state how many of those are customer-callable"

    assert _words(sites.group(1)) == len(rows), (
        f"§13 says {sites.group(1)} read sites and its table carries {len(rows)} "
        "rows — the count is the ruling's argument, so it is asserted, not "
        "prose (it was wrong twice: B2.7 said two customer-callable sites, "
        "#7956 corrected it to four)."
    )
    counted = sum(1 for row in rows if _reach(row) == "customer-callable")
    assert _words(callable_sites.group(1)) == counted, (
        f"§13 says {callable_sites.group(1)} customer-callable read sites and "
        f"{counted} rows are classed customer-callable"
    )


# ── 5. every customer-callable row reaches a declared door ─────────────────


def _public_sdk_methods() -> set[str]:
    tree = _module_tree(PKG / "sdk.py")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK":
            return {
                inner.name
                for inner in node.body
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not inner.name.startswith("_")
            }
    raise AssertionError("TortoiseSDK not found in tortoise/sdk.py")


def _registered_tools() -> set[str]:
    tree = _module_tree(PKG / "tool_registry.py")
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ToolDefinition"
        ):
            for keyword in node.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    names.add(str(keyword.value.value))
    assert names, "no ToolDefinition names found in tortoise/tool_registry.py"
    return names


def test_every_customer_callable_row_reaches_a_declared_door():
    sdk_methods = _public_sdk_methods()
    tools = _registered_tools()
    broken: list[str] = []

    for row in _rows():
        if _reach(row) != "customer-callable":
            continue
        label = f"row {row['n']} ({row['module']}::{row['symbol']})"
        sdk = re.findall(r"SDK `([A-Za-z_][\w]*)`", str(row["reach"]))
        mcp = re.findall(r"MCP `([A-Za-z_][\w]*)`", str(row["reach"]))
        if not sdk and not mcp:
            broken.append(f"{label} — names no door (SDK/MCP backticked name)")
            continue
        broken.extend(
            f"{label} — SDK `{name}` is not a public TortoiseSDK method"
            for name in sdk
            if name not in sdk_methods
        )
        broken.extend(
            f"{label} — MCP `{name}` is not a registered tool"
            for name in mcp
            if name not in tools
        )

    assert not broken, (
        "§13 classes these rows customer-callable without naming a door that "
        "exists:\n  " + "\n  ".join(broken)
    )


# ── 6. the carrier constants really carry the edge ─────────────────────────


def test_every_carrier_constant_a_row_reads_through_still_names_the_edge():
    """The constant IS the edge name — its membership is the read, not a detail.

    Three `recall_state` readers and the supersede path name `aboutObject` in a
    constant, never in a query, so a literal census (and any grep) misses them.
    That is how the customer-callable count stayed at four through three
    reviews. If the edge leaves one of these constants, the §13 row keeps
    resolving while the site stops reading the edge — an under-count in the
    direction that matters.

    The candidates come from `_carrier_shaped_constants()` (shape + `about*`
    family), NOT from `_carrier_constants()` (which filters on the very name
    this test asserts). Deleting the edge from `ABOUT_EDGE_TYPES` therefore
    reds here — measured: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest
    tests/test_7948_about_object_read_sites.py -q` goes 8 passed → 1 failed
    with `row 17: … ABOUT_EDGE_TYPES`.
    """
    broken: list[str] = []
    for row in _rows():
        module = str(row["module"])
        tree = _module_tree(_module_path(module))
        span = _span(tree, str(row["symbol"]))
        if span is None:
            continue  # covered by the symbol test
        source = "\n".join(
            _module_path(module).read_text(encoding="utf-8").splitlines()[span[0] - 1 : span[1]]
        )
        carriers = sorted(
            qualified
            for qualified in _carrier_shaped_constants(module)
            if re.search(rf"\b{re.escape(qualified.split('.')[-1])}\b", source)
        )
        for carrier in carriers:
            declaration = _span(tree, carrier)
            assert declaration is not None, f"{module}::{carrier} does not resolve"
            declared = "\n".join(
                _module_path(module)
                .read_text(encoding="utf-8")
                .splitlines()[declaration[0] - 1 : declaration[1]]
            )
            if "aboutObject" not in declared:
                broken.append(f"row {row['n']}: {module}::{row['symbol']} reads through {carrier}")
    assert not broken, (
        "these readers name the edge through a constant that NO LONGER contains "
        "`aboutObject`:\n  " + "\n  ".join(broken)
    )


# ── 7. the sibling document states the same fact ───────────────────────────


def test_the_extractor_document_agrees_on_the_count():
    """§13.8 of `EXTRACTOR-V4-ARCHITECTURE.md` carries the same census.

    Two live documents stating one fact is how this under-count survived three
    reviews: a repair that corrects §13 and leaves §13.8 asserting the old
    number re-creates exactly the contradiction `#7956` had to correct. The
    sibling must therefore state the §13 count, and must name the state-recall
    readers that the four-site census missed.
    """
    extractor = (
        ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"
    ).read_text(encoding="utf-8")
    counted = sum(1 for row in _rows() if _reach(row) == "customer-callable")
    word = next(word for word, value in _NUMBER_WORDS.items() if value == counted)

    stated = {
        _words(m.group(1))
        for m in re.finditer(
            r"\b([A-Z]+|\d+) (?:sites?|of them) (?:are|is) \*?customer-callable",
            extractor,
            re.I,
        )
    }
    assert stated == {counted}, (
        f"EXTRACTOR-V4-ARCHITECTURE.md states customer-callable counts {sorted(stated)} "
        f"and §13's table counts {counted} ({word}) — the two documents must state "
        "ONE fact, and it must be the one the table enumerates"
    )
    for reader in ("_fetch_object_signals", "_state_related_objects", "_state_related_points"):
        assert reader in extractor, (
            f"EXTRACTOR-V4-ARCHITECTURE.md does not name `{reader}` — a "
            "`recall_state` read the four-site census missed"
        )
